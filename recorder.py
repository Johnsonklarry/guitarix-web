"""
Recording, via ffmpeg reading straight off JACK.

ffmpeg registers itself as a JACK client and exposes input ports; guitarix's
outputs get wired to them with jack_connect once those ports appear. Files
land in RECORDINGS_DIR as WAV.

WAV rather than FLAC or mp3 on purpose: the Pi is already running a realtime
audio graph, and an encoder competing for CPU is a good way to earn xruns
mid-take. WAV costs disk instead, which is the cheaper resource here. Change
CONTAINER / FFMPEG_ARGS below if you'd rather trade the other way.

If your ffmpeg wasn't built with JACK support (`ffmpeg -devices` will say),
swap FFMPEG_ARGS for a jack_capture or ALSA invocation — anything that writes
one file and stops cleanly on SIGINT will work.
"""

import datetime
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time

import jackutil

log = logging.getLogger("recorder")

RECORDINGS_DIR = os.path.expanduser("~/recordings")
CONTAINER = "wav"
DRY_SUFFIX = " (dry)"          # marks a take's unprocessed twin

# JACK client names ffmpeg registers under: one for the wet signal, one for
# the dry. JACK won't let two clients share a name. (Playing files back into
# JACK is mpv's job, in player.py -- ffmpeg can't output to JACK.)
JACK_CLIENT = "gxweb"
JACK_CLIENT_DRY = "gxweb-dry"

# Where the wet (processed) signal comes from: guitarix's own output. The
# real port names are discovered with jack_lsp at record time; SOURCE_PORTS
# is only the fallback for when jack_lsp isn't around.
SOURCE_CLIENT = "gx_head_amp"
SOURCE_PORTS = ["gx_head_amp:out_0", "gx_head_amp:out_1"]

# Where the dry (unprocessed) signal comes from: the audio interface's own
# capture ports. In JACK these are OUTPUT-typed ports -- they source the
# signal into the graph, even though the human name for them is "capture" --
# so the same fan-out rule applies as for SOURCE_PORTS: a second reader here
# doesn't take anything away from guitarix's own connection to the same
# ports. Run `jack_lsp -p` to see what yours are actually called; "system"
# is JACK's usual name for the hardware interface, but a USB interface or a
# different audio backend can register under something else entirely.
DRY_CLIENT = "system"
DRY_PORTS = ["system:capture_1", "system:capture_2"]


# A take's life: it starts, its audio gets wired in, the writer opens the
# file, the engine is asked to stop and close the header, and only then is it
# something anyone can play. Recorder.state exposes where it currently is,
# and listing() publishes a take only once it reaches READY -- a half-written
# WAV, or one whose audio never arrived, is not a take. ERROR is where a take
# ends up when something went wrong; it is never published.
STATE_STARTING = "starting"
STATE_WIRED = "wired"
STATE_WRITING = "writing"
STATE_FINALIZING = "finalizing"
STATE_READY = "ready"
STATE_ERROR = "error"
STATES = (STATE_STARTING, STATE_WIRED, STATE_WRITING, STATE_FINALIZING, STATE_READY)
TERMINAL_STATES = (STATE_READY, STATE_ERROR)

# How long the source's ports get to appear and accept the connection before
# the take is called unwireable.
WIRING_TIMEOUT = 5

# How long ffmpeg gets to close the WAV header once it has been asked to
# stop, and then how long the kill gets to land. stop() blocks for both at
# worst, so it can never hang on a wedged engine.
FINALIZE_TIMEOUT = 5
KILL_TIMEOUT = 3

# How long the writer gets to open the take's file before the take counts as
# writing anyway; the engine may buffer a moment before the first byte lands.
WRITER_TIMEOUT = 1.0


FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# "Attempt 12", "Attempt 12 (dry)", "Attempt 12 (render)" all count as 12
ATTEMPT = re.compile(r"^Attempt (\d+)\b")
SAFE_NAME = re.compile(r"[^A-Za-z0-9 _.\-()]")
MAX_NAME = 80


def _record_args(client, path):
    """ffmpeg as a JACK capture device: registers `client`'s input ports,
    and whatever gets connected into them is what lands in `path`."""
    return [FFMPEG, "-hide_banner", "-loglevel", "warning",
            "-f", "jack", "-i", client, "-ac", "2", "-y", path]


def safe_name(name, fallback="take"):
    """Strip a user-supplied name down to something that can't escape the dir."""
    name = os.path.basename(name or "").strip()
    name = SAFE_NAME.sub("", name)[:MAX_NAME].strip(" .")
    return name or fallback


class Recorder:
    def __init__(self, directory=RECORDINGS_DIR, on_change=None):
        self.dir = os.path.abspath(os.path.expanduser(directory))
        os.makedirs(self.dir, exist_ok=True)
        self.on_change = on_change or (lambda: None)

        self._proc = None
        self._dry_proc = None
        self._path = None
        self._dry_path = None
        self._started = None
        self._error = None
        self._wiring = None
        self._dry_wiring = None
        self._state = None           # where the current take is, see STATES
        self._sidecar = None         # the take's settings file, if it has one
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._durations = {}          # path -> (mtime, seconds)

    # ------------------------------------------------------------ status

    @property
    def recording(self):
        # Read _proc once: the ticker calls this without the lock while
        # stop() can clear it, so a second read could see None and crash.
        proc = self._proc
        return proc is not None and proc.poll() is None

    @property
    def state(self):
        """
        Where the current take is: one of STATES, or STATE_ERROR if it came
        off the rails. None before the first start(). Readable at any point,
        from any thread -- that is the whole point of it.
        """
        return self._state

    def _set_state(self, state, force=False):
        """
        Move the take along. A take that reached a terminal state stays
        there: nothing later on -- a straggling wiring thread, a late watch
        thread -- gets to turn a published or a failed take into something
        else. Only start() forces a reset, for the next take.
        """
        with self._state_lock:
            if not force and self._state in TERMINAL_STATES:
                return
            log.debug("take state: %s", state)
            self._state = state

    def _fail(self, message, state=STATE_ERROR):
        """Park the take where listing() won't publish it, and say why."""
        if self._error is None:
            self._error = message
        self._set_state(state)
        log.error("take failed: %s", message)

    def status(self):
        return {
            "recording": self.recording,
            "file": os.path.basename(self._path) if self._path and self.recording else None,
            "state": self._state,
            "dry": self._dry_proc is not None and self._dry_proc.poll() is None,
            "elapsed": round(time.time() - self._started, 1) if self.recording and self._started else 0,
            "error": self._error,
            "wiring": self._wiring,
            "available": shutil.which(FFMPEG) is not None,
            "dry_available": bool(self._jack_ports(DRY_CLIENT, "output", DRY_PORTS)),
            "next_attempt": self.next_attempt(),
            "free_bytes": self._free_bytes(),
            "count": self.take_count(),
        }

    # ------------------------------------------------------------ recording

    def start(self, name=None, dry=False, sidecar=None):
        """
        Begin recording. `dry` also captures the unprocessed signal straight
        off the interface, alongside the normal (wet, processed) file, so a
        performance can be run through different settings later. `sidecar`,
        if given, is written as JSON next to the wet file: the live settings
        at the moment recording started. Nothing here reads it back -- it's
        there for you, and for reamping to show what a take was made with --
        default name from.
        """
        with self._lock:
            if self.recording:
                return self.status()

            base = safe_name(name, fallback=self.attempt_name()) if name else self.attempt_name()
            path = self._unique(os.path.join(self.dir, base + "." + CONTAINER))
            stem, ext = os.path.splitext(path)

            self._error = None
            self._wiring = None
            self._dry_wiring = None
            self._sidecar = None
            self._set_state(STATE_STARTING, force=True)
            log.info("recording to %s", path)
            try:
                self._proc = subprocess.Popen(
                    _record_args(JACK_CLIENT, path),
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except FileNotFoundError:
                self._error = "ffmpeg is not installed"
                log.error(self._error)
                return self.status()

            self._path = path
            self._started = time.time()

            self._dry_proc = None
            self._dry_path = None
            if dry:
                dry_path = stem + DRY_SUFFIX + ext
                try:
                    self._dry_proc = subprocess.Popen(
                        _record_args(JACK_CLIENT_DRY, dry_path),
                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    self._dry_path = dry_path
                except FileNotFoundError:
                    pass          # the dry capture is a bonus; the wet one still goes ahead

            if sidecar is not None:
                self._sidecar = stem + ".json"
                try:
                    with open(self._sidecar, "w") as f:
                        json.dump(sidecar, f, indent=1)
                except OSError:
                    log.warning("couldn't write the settings sidecar for %s", path)

        # Take the answer before the wiring threads get going, so start()
        # reports the take as it stands now -- "starting" -- rather than as it
        # might look a moment later.
        status = self.status()

        threading.Thread(target=self._wire, args=(SOURCE_CLIENT, SOURCE_PORTS, JACK_CLIENT,
                         "_wiring", "the amp"), daemon=True).start()
        threading.Thread(target=self._watch, args=(self._proc, "_error"), daemon=True).start()
        if self._dry_proc:
            threading.Thread(target=self._wire, args=(DRY_CLIENT, DRY_PORTS, JACK_CLIENT_DRY,
                             "_dry_wiring", "the interface"), daemon=True).start()
            threading.Thread(target=self._watch, args=(self._dry_proc, None), daemon=True).start()
        self.on_change()
        return status

    def stop(self):
        with self._lock:
            proc, dry_proc = self._proc, self._dry_proc
            if proc is None or proc.poll() is not None:
                return self.status()
            log.info("stopping recording")
            self._set_state(STATE_FINALIZING)
            for p in (proc, dry_proc):
                if p is None:
                    continue
                try:
                    p.send_signal(signal.SIGINT)      # lets ffmpeg close the header
                except OSError:
                    pass

        stalled = False
        for p in (proc, dry_proc):
            if p is None:
                continue
            try:
                p.wait(timeout=FINALIZE_TIMEOUT)
            except subprocess.TimeoutExpired:
                log.warning("ffmpeg didn't stop, killing it")
                stalled = True
                p.kill()
                try:
                    p.wait(timeout=KILL_TIMEOUT)
                except subprocess.TimeoutExpired:
                    log.warning("ffmpeg ignored the kill; the take is incomplete")

        if stalled:
            self._fail("the engine didn't stop cleanly; the take wasn't published")
        else:
            self._finish()

        self.on_change()
        return self.status()

    def _watch(self, proc, error_attr):
        """Notice a crash (bad ffmpeg build, JACK gone) and surface the reason."""
        _, err = proc.communicate()
        code = proc.returncode
        # SIGINT is how we ask it to stop, so that exit is expected
        if error_attr and code not in (0, -signal.SIGINT, 255) and getattr(self, error_attr) is None:
            tail = (err or b"").decode("utf-8", "replace").strip().splitlines()
            setattr(self, error_attr, tail[-1] if tail else ("ffmpeg exited with %s" % code))
            log.error("ffmpeg: %s", getattr(self, error_attr))
            if proc is self._proc:
                self._set_state(STATE_ERROR)     # a dead engine is not a take
        if proc is self._proc:
            self._started = None
        self.on_change()

    # ------------------------------------------------------------ jack plumbing

    def _jack_ports(self, client, direction, fallback):
        return jackutil.ports(client, direction, fallback)

    def _wire(self, client, fallback_ports, jack_client, wiring_attr, source_label):
        """
        Connect `client`'s output ports to both of `jack_client`'s inputs.

        Two things this has to get right. ffmpeg's ports don't exist until it
        has registered with JACK, so we poll for them. And if the source is
        mono -- common on a headless amp, or a mono interface -- that one
        port goes to BOTH inputs, otherwise the take is silent on one side.
        """
        setattr(self, wiring_attr, None)
        if not shutil.which("jack_connect"):
            setattr(self, wiring_attr, "jack_connect not installed; patch the ports yourself")
            return

        inputs = ["%s:input_1" % jack_client, "%s:input_2" % jack_client]
        deadline = time.time() + WIRING_TIMEOUT

        while time.time() < deadline:
            sources = self._jack_ports(client, "output", fallback_ports)
            if not sources:
                time.sleep(0.25)
                continue

            pairs = ([(sources[0], inputs[0]), (sources[0], inputs[1])] if len(sources) == 1
                     else list(zip(sources, inputs)))

            connected = []
            for src, dst in pairs:
                rc = subprocess.run(["jack_connect", src, dst], capture_output=True).returncode
                if rc == 0:
                    connected.append(dst)

            if set(connected) >= set(inputs):
                how = "mono source on both channels" if len(sources) == 1 else "stereo"
                log.info("wired %s -> %s (%s)", sources[:2], jack_client, how)
                setattr(self, wiring_attr, None if len(sources) > 1 else
                       "one source port from %s, recorded to both channels" % source_label)
                if jack_client == JACK_CLIENT:
                    self._wired()
                return
            time.sleep(0.25)

        msg = "couldn't wire both channels from %s into %s; check jack_lsp" % (source_label, jack_client)
        setattr(self, wiring_attr, msg)
        log.warning(msg)
        if jack_client == JACK_CLIENT:
            # nobody ever fed the take: it stays unpublished, and the reason
            # goes where every other fault goes, the recorder's error field
            self._fail(msg)

    def _wired(self):
        """
        Both of the wet client's inputs are patched, so the take is live. The
        writer takes a moment to open the file after the engine registers, so
        give it that moment before calling the take "writing" -- but never
        wait past WRITER_TIMEOUT, and never override a take that has already
        been stopped or has failed underneath us.
        """
        self._set_state(STATE_WIRED)
        deadline = time.time() + WRITER_TIMEOUT
        while time.time() < deadline:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return                     # the watcher reports what happened
            if self._path and os.path.exists(self._path):
                break
            time.sleep(0.05)
        if self._state == STATE_WIRED:
            self._set_state(STATE_WRITING)

    def _finish(self):
        """
        The engine is gone, so the WAV header is closed. Publish the take only
        if what it should have left behind is really there: the audio, and the
        settings sidecar if one was asked for.
        """
        name = os.path.basename(self._path) if self._path else "the take"
        if not self._path or not os.path.isfile(self._path):
            self._fail("no recording was written for %s" % name)
            return
        if self._sidecar and not os.path.isfile(self._sidecar):
            self._fail("the settings sidecar for %s wasn't written" % name)
            return
        self._set_state(STATE_READY)

    # ------------------------------------------------------------ library

    def _resolve(self, name):
        """Turn a client-supplied name into a path inside the directory, or None."""
        path = os.path.abspath(os.path.join(self.dir, os.path.basename(name or "")))
        if os.path.dirname(path) != self.dir or not os.path.isfile(path):
            return None
        return path

    def _unique(self, path):
        stem, ext = os.path.splitext(path)
        n = 2
        while os.path.exists(path):
            path = "%s (%d)%s" % (stem, n, ext)
            n += 1
        return path

    def next_attempt(self):
        """One past the highest "Attempt N" already in the folder."""
        highest = 0
        for entry in os.listdir(self.dir):
            m = ATTEMPT.match(os.path.splitext(entry)[0])
            if m:
                highest = max(highest, int(m.group(1)))
        return highest + 1

    def attempt_name(self):
        return "Attempt %d" % self.next_attempt()

    def recorder_inputs(self):
        """The recorder's JACK inputs, while it's running -- for adding a backing track to a take."""
        return jackutil.ports(JACK_CLIENT, "input") if self.recording else []

    def take_count(self):
        """
        How many wet takes are in the folder, the one rolling included. The
        same entries listing() shows (no dry twins, no sidecars), counted
        without probing durations, so it is cheap enough for every status().
        """
        try:
            return sum(1 for e in os.scandir(self.dir)
                       if e.is_file() and not e.name.startswith(".")
                       and not e.name.endswith(".json")
                       and not os.path.splitext(e.name)[0].endswith(DRY_SUFFIX))
        except OSError:
            return None

    def _free_bytes(self):
        try:
            return shutil.disk_usage(self.dir).free
        except OSError:
            return None

    def _siblings(self, name):
        """
        A wet file's dry twin and settings sidecar, whichever exist. Both are
        addressed by name rather than discovered by scanning, so a file that
        merely happens to share a prefix isn't mistaken for one.
        """
        stem, ext = os.path.splitext(name)
        dry = os.path.join(self.dir, stem + DRY_SUFFIX + ext)
        sidecar = os.path.join(self.dir, stem + ".json")
        return (dry if os.path.isfile(dry) else None,
               sidecar if os.path.isfile(sidecar) else None)

    def listing(self):
        """
        One entry per wet recording. A dry twin or a settings sidecar isn't
        listed on its own -- it's attached to its wet file's entry instead,
        as "dry" / "settings" (the sidecar's filename, or None).
        """
        entries = {e.name: e for e in os.scandir(self.dir) if e.is_file() and not e.name.startswith(".")}
        current = os.path.basename(self._path) if self.recording and self._path else None
        # A take is published only once its state machine says so: until it
        # reaches "ready" its file may be a half-written WAV, or one whose
        # audio never arrived at all.
        rolling = os.path.basename(self._path) if self._path else None
        ready = self._state == STATE_READY

        items = []
        for name, entry in entries.items():
            stem = os.path.splitext(name)[0]
            if name.endswith(".json") or stem.endswith(DRY_SUFFIX):
                continue
            if name == rolling and not ready:
                continue
            stat = entry.stat()
            dry, sidecar = self._siblings(name)
            items.append({
                "name": name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "duration": self._duration(entry.path, stat.st_mtime),
                "active": name == current,
                "dry": os.path.basename(dry) if dry else None,
                "settings": os.path.basename(sidecar) if sidecar else None,
            })
        items.sort(key=lambda i: i["modified"], reverse=True)
        return items

    def _duration(self, path, mtime):
        cached = self._durations.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        seconds = None
        if shutil.which(FFPROBE):
            try:
                out = subprocess.run(
                    [FFPROBE, "-v", "quiet", "-print_format", "json",
                     "-show_format", path],
                    capture_output=True, timeout=5)
                seconds = float(json.loads(out.stdout)["format"]["duration"])
            except (ValueError, KeyError, OSError, subprocess.TimeoutExpired):
                seconds = None
        self._durations[path] = (mtime, seconds)
        return seconds

    def rename(self, name, new_name):
        path = self._resolve(name)
        if not path:
            raise FileNotFoundError(name)
        dry, sidecar = self._siblings(name)
        ext = os.path.splitext(path)[1]
        target = self._unique(os.path.join(self.dir, safe_name(new_name) + ext))
        os.rename(path, target)
        self._durations.pop(path, None)
        new_stem = os.path.splitext(target)[0]
        # best-effort: the wet file is what matters, and its rename already
        # succeeded even if a sibling can't be moved for some reason
        if dry:
            try:
                os.rename(dry, new_stem + DRY_SUFFIX + ext)
            except OSError:
                log.warning("renamed %s but couldn't move its dry twin", name)
        if sidecar:
            try:
                os.rename(sidecar, new_stem + ".json")
            except OSError:
                log.warning("renamed %s but couldn't move its settings", name)
        self.on_change()
        return os.path.basename(target)

    def delete(self, name):
        self._remove(name)
        self.on_change()

    def _remove(self, name):
        path = self._resolve(name)
        if not path:
            raise FileNotFoundError(name)
        if self.recording and path == self._path:
            raise RuntimeError("that take is still recording")
        dry, sidecar = self._siblings(name)
        os.remove(path)
        self._durations.pop(path, None)
        for extra in (dry, sidecar):
            if extra:
                try:
                    os.remove(extra)
                except OSError:
                    log.warning("deleted %s but couldn't remove %s", name, extra)

    def delete_many(self, names):
        """
        Delete several takes (each with its dry twin and settings) and tell the
        clients once. Every name goes through the same containment check as a
        single delete (_resolve), so nothing outside the recordings folder can
        be reached. One bad entry doesn't stop the rest.

        Returns (deleted, failed): names that went, and (name, reason) pairs.
        """
        deleted, failed, seen = [], [], set()
        for name in names:
            if not isinstance(name, str) or name in seen:
                continue
            seen.add(name)
            try:
                self._remove(name)
            except FileNotFoundError:
                failed.append((name, "not found"))
            except (RuntimeError, OSError) as exc:
                failed.append((name, str(exc)))
            else:
                deleted.append(name)
        if deleted:
            self.on_change()
        return deleted, failed
