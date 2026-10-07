"""
Playing an audio file into JACK, via mpv.

Why mpv: ffmpeg can READ from JACK -- that's how recording works -- but it has
no JACK output device at all (`ffmpeg -devices` lists jack as input-only).
mpv has a real JACK output, and a JSON control socket for changing things
while it plays: pause, seek, volume, looping.

A Player plays one file at a time as its own JACK client, and doesn't decide
where the sound goes. The caller passes destination ports, and can change
them mid-playback with route(). The same player serves three purposes:

    backing track  -> the interface's outputs, and optionally the recorder
    reamp, wet     -> guitarix's input, so it's heard through the amp
    reamp, dry     -> the interface's outputs, bypassing the amp

Check mpv on the Pi has JACK:   mpv --ao=help | grep jack
"""

import json
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time

import jackutil

log = logging.getLogger("player")

MPV = "mpv"

# ------------------------------------------------------------------- flags
#
# What player.py is allowed to put on an mpv command line, and what a latency
# benchmark is allowed to vary. mpv ignores flags it doesn't know and only
# warns, so a typo -- or a flag nobody ever measured -- would otherwise turn up
# in a report looking like a measurement. Naming the schema here makes that a
# loud failure at the point the command line is built.
#
# mpv has two kinds of flag -- the ones that stand alone and the ones that want
# a value -- so there are two tables. Anything in neither one is undocumented.

MPV_BOOLEAN_FLAGS = frozenset({
    "--no-config",        # ours, not whatever is in the user's mpv.conf
    "--no-video",
    "--no-terminal",
    "--really-quiet",
    "--pause",
    "--jack-connect",     # we do the wiring, so it lands where we choose
    "--jack-autostart",   # never start a second JACK server by accident
    "--no-audio-display",
    "--gapless-audio",
})

_MPV_ON_OFF = frozenset({"yes", "no", "true", "false", "1", "0"})


def _is_number(value):
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


# flag -> what its value has to look like.
MPV_VALUE_FLAGS = {
    "--ao": lambda value: value == "jack",
    "--jack-name": lambda value: value != "",
    "--jack-port": lambda value: value != "",
    "--audio-buffer": _is_number,          # seconds; the benchmark's knob
    "--input-ipc-server": lambda value: value != "",
    "--volume": _is_number,
    "--loop-file": lambda value: value in ("no", "inf"),
}

MPV_FLAGS = frozenset(MPV_BOOLEAN_FLAGS) | frozenset(MPV_VALUE_FLAGS)


def validate_mpv_flags(flags):
    """True if every flag is one we've measured; ValueError on the first that isn't.

    Positional arguments (the file to play) are ignored: mpv_args() puts the
    path at the end of its list and it is not a flag.
    """
    for flag in flags:
        if not isinstance(flag, str):
            raise ValueError("mpv flags must be strings, got %r" % (flag,))
        if not flag.startswith("--"):
            continue
        name, sep, value = flag.partition("=")
        if name in MPV_BOOLEAN_FLAGS:
            if sep and value not in _MPV_ON_OFF:
                raise ValueError("%s takes yes or no, not %r" % (name, value))
            continue
        allowed = MPV_VALUE_FLAGS.get(name)
        if allowed is None:
            raise ValueError("undocumented mpv flag: %s" % name)
        if not sep or not allowed(value):
            raise ValueError("mpv flag %s got an undocumented value: %r" % (name, value))
    return True


def mpv_args(client, sock, path, volume, loop, audio_buffer=None):
    """The mpv command line for one player.

    `audio_buffer`, when given, is in seconds and sets mpv's own buffer in
    front of JACK -- the knob the latency benchmark varies. Leaving it out
    keeps mpv's default, which is what ordinary playback uses.
    """
    args = [
        MPV, "--no-config", "--no-video", "--no-terminal", "--really-quiet",
        "--ao=jack", "--jack-name=" + client,
        "--jack-connect=no",          # we do the wiring, so it lands where we choose
        "--jack-autostart=no",        # never start a second JACK server by accident
        "--input-ipc-server=" + sock,
        "--pause",                    # hold still until the ports are wired
        "--volume=%d" % max(0, min(100, int(volume))),
        "--loop-file=" + ("inf" if loop else "no"),
        path,
    ]
    if audio_buffer is not None:
        args.insert(-1, "--audio-buffer=%s" % audio_buffer)
    return args


class PlayerError(Exception):
    pass


class _Ipc:
    """mpv's JSON IPC: one command per line, replies matched by request_id."""

    def __init__(self, path, timeout=4.0):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(path)
                s.settimeout(2.0)
                self._sock, self._buf = s, b""
                self._lock, self._n = threading.Lock(), 0
                return
            except OSError as exc:
                s.close()
                last = exc
                time.sleep(0.05)
        raise PlayerError("mpv's control socket never appeared (%s)" % last)

    def command(self, *args):
        with self._lock:
            self._n += 1
            rid = self._n
            self._sock.sendall((json.dumps({"command": list(args), "request_id": rid}) + "\n").encode())
            while True:
                while b"\n" not in self._buf:
                    chunk = self._sock.recv(65536)
                    if not chunk:
                        raise PlayerError("mpv closed its control socket")
                    self._buf += chunk
                line, _, self._buf = self._buf.partition(b"\n")
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("request_id") != rid:
                    continue              # an event, or an answer to someone else
                if msg.get("error") not in (None, "success"):
                    raise PlayerError(msg["error"])
                return msg.get("data")

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


class Player:
    def __init__(self, client, on_change=None, on_end=None):
        self.client = client
        self.on_change = on_change or (lambda: None)
        self.on_end = on_end or (lambda reason: None)
        self._proc = self._ipc = None
        self._path = None
        self._targets = []
        self._volume, self._loop = 100, False
        self._stopping = False
        self._lock = threading.Lock()
        self._play_lock = threading.Lock()

    @staticmethod
    def installed():
        return shutil.which(MPV) is not None

    @property
    def playing(self):
        # Read _proc once: the ticker calls this without the lock while
        # stop() can clear it, so a second read could see None and crash.
        proc = self._proc
        return proc is not None and proc.poll() is None

    # ------------------------------------------------------------ control

    def play(self, path, targets, volume=100, loop=False):
        """Start `path`, wired to `targets` before any sound comes out."""
        with self._play_lock:
            self.stop()
            if not self.installed():
                raise PlayerError("mpv isn't installed on the Pi (sudo apt install mpv)")

            sock = os.path.join(tempfile.gettempdir(), "gxweb-%s-%d.sock" % (self.client, os.getpid()))
            try:
                os.unlink(sock)
            except OSError:
                pass

            with self._lock:
                self._stopping = False
                self._volume, self._loop = volume, loop
                self._path, self._targets = path, list(targets)
                try:
                    self._proc = subprocess.Popen(
                        mpv_args(self.client, sock, path, volume, loop),
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                except OSError as exc:
                    self._proc = None
                    raise PlayerError("couldn't start mpv: %s" % exc)

            try:
                self._ipc = _Ipc(sock)
                outs = self._wait_for_ports(paused=True)
                if not outs:
                    # some builds only open the audio output once playing: start,
                    # wire, then go back to the top so nothing is lost
                    self._ipc.command("set_property", "pause", False)
                    outs = self._wait_for_ports(paused=False)
                    if not outs:
                        raise PlayerError("mpv never showed up in JACK — is its JACK output working?")
                    self._wire(outs, self._targets)
                    self._ipc.command("seek", 0, "absolute")
                else:
                    self._wire(outs, self._targets)
                    self._ipc.command("set_property", "pause", False)
            except PlayerError:
                self._kill()
                raise

            threading.Thread(target=self._watch, args=(self._proc,), daemon=True).start()
            self.on_change()

    def route(self, targets):
        """Send the sound somewhere else, mid-playback. Old wiring comes down."""
        with self._lock:
            old, self._targets = self._targets, list(targets)
        if not self.playing:
            return
        outs = jackutil.ports(self.client, "output")
        for src, dst in jackutil.pairs(outs, old):
            jackutil.disconnect(src, dst)
        self._wire(outs, self._targets)
        self.on_change()

    def add_targets(self, extra):
        """Also feed `extra` — the recorder, say -- without touching the rest."""
        extra = [t for t in extra if t not in self._targets]
        if not extra or not self.playing:
            return
        self._targets.extend(extra)
        self._wire(jackutil.ports(self.client, "output"), extra)

    def pause(self, paused=True):
        self._set("pause", bool(paused))

    def seek(self, seconds):
        if self._ipc and self.playing:
            try:
                self._ipc.command("seek", max(0.0, float(seconds)), "absolute")
            except PlayerError as exc:
                log.warning("seek failed: %s", exc)
        self.on_change()

    def set_volume(self, volume):
        self._volume = max(0, min(100, int(volume)))
        return self._set("volume", self._volume)

    def set_loop(self, loop):
        self._loop = bool(loop)
        return self._set("loop-file", "inf" if loop else "no")

    def stop(self):
        with self._lock:
            if not self.playing:
                return
            self._stopping = True
        try:
            self._ipc.command("quit")
        except (PlayerError, AttributeError, OSError):
            pass
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._kill()

    # ------------------------------------------------------------ status

    def status(self):
        """
        What mpv is actually doing, asked of mpv. Reporting what we last told
        it to do instead would mean a setting it rejected still showed as on
        -- the button lights up and nothing loops.
        """
        if not self.playing:
            return None
        loop = self._get("loop-file")
        volume = self._get("volume")
        return {
            "file": os.path.basename(self._path or ""),
            "position": self._get("time-pos"),
            "duration": self._get("duration"),
            "paused": bool(self._get("pause")),
            "volume": int(volume) if volume is not None else self._volume,
            "loop": (loop not in (None, "no", False)) if loop is not None else self._loop,
        }

    # ------------------------------------------------------------ internals

    def _set(self, prop, value):
        """True if mpv took it. Failures are reported, not swallowed."""
        ok = True
        if self._ipc and self.playing:
            try:
                self._ipc.command("set_property", prop, value)
            except PlayerError as exc:
                log.warning("mpv wouldn't set %s to %r: %s", prop, value, exc)
                ok = False
        self.on_change()
        return ok

    def _get(self, prop):
        if not (self._ipc and self.playing):
            return None
        try:
            value = self._ipc.command("get_property", prop)
        except (PlayerError, OSError):
            return None
        return round(value, 2) if isinstance(value, float) else value

    def _wait_for_ports(self, paused, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline and self.playing:
            outs = jackutil.ports(self.client, "output")
            if outs:
                return outs
            time.sleep(0.1)
        return []

    def _wire(self, outs, targets):
        for src, dst in jackutil.pairs(outs, targets):
            if not jackutil.connect(src, dst):
                log.warning("couldn't connect %s -> %s", src, dst)

    def _kill(self):
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def _watch(self, proc):
        _, err = proc.communicate()
        with self._lock:
            if proc is not self._proc:
                return                    # an old player; a newer one is in charge
            stopped = self._stopping
            self._proc = None
        if self._ipc:
            self._ipc.close()
            self._ipc = None
        if stopped:
            reason = "stopped"
        elif proc.returncode == 0:
            reason = "finished"
        else:
            tail = (err or b"").decode("utf-8", "replace").strip().splitlines()
            reason = "failed: " + (tail[-1] if tail else "mpv exited with %s" % proc.returncode)
        self.on_end(reason)
        self.on_change()
