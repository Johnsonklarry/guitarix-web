"""
Reamping: play a dry take back through the amp, in real time.

The dry file goes into guitarix's input where your guitar normally is, so you
hear it through whatever's dialed in, live -- change presets, move faders,
switch effects while it plays and hear it straight away. Nothing is rendered
ahead of time.

"Amp off" routes the same playback straight to the interface outputs instead,
so you can flip between the raw take and the amped one mid-phrase.

Tick "record" and the pass is also captured as a new take, which is what the
old deferred renderer did -- now it's just a reamp you happen to record.

While a session runs, your guitar is disconnected from the amp: guitarix has
one input, and a take and a guitar arriving at once would just be a mess.
Its wiring is saved first and put back when the session ends -- when the
file finishes, when you stop it, or when anything goes wrong.
"""

import logging
import threading

import jackutil
from player import Player, PlayerError
from recorder import SOURCE_CLIENT

log = logging.getLogger("reamp")

AMP_CLIENT = SOURCE_CLIENT          # guitarix, whose input the take goes into
OUTPUT_CLIENT = "system"            # the interface, for "amp off"
FALLBACK_OUTPUTS = ["system:playback_1", "system:playback_2"]


class ReampError(Exception):
    pass


class Reamp:
    def __init__(self, recorder, on_change=None):
        self.rec = recorder
        self.on_change = on_change or (lambda: None)
        self.player = Player("gxweb-reamp", on_change=self.on_change, on_end=self._ended)
        self._lock = threading.Lock()
        self._session = None   # {"take", "mode", "record", "output", "saved"}
        self.restore_error = None   # set when the last session couldn't rewire the guitar

    @property
    def active(self):
        return self._session is not None

    def status(self):
        s = self._session
        if not s:
            return None
        playback = self.player.status() or {}
        return {"take": s["take"], "mode": s["mode"], "record": s["record"],
                "output": s["output"], "position": playback.get("position"),
                "duration": playback.get("duration"), "paused": playback.get("paused", False),
                "loop": playback.get("loop", False)}

    # ------------------------------------------------------------ control

    def start(self, take, dry_name, mode="wet", record=False, loop=False, name=None):
        """
        Play `dry_name` -- the dry twin of the take called `take` -- through the
        amp. `record` captures the pass as a new take; a recorded pass can't
        loop, since it has to end for the take to be finished.
        """
        with self._lock:
            if self._session:
                raise ReampError("already reamping %s" % self._session["take"])
            if record and self.rec.recording:
                raise ReampError("stop the current recording first")
            path = self.rec._resolve(dry_name)
            if not path:
                raise ReampError("can't find the dry recording for %s" % take)
            if not jackutil.available():
                raise ReampError("the JACK tools (jack_lsp, jack_connect) aren't installed")
            amp_inputs = jackutil.ports(AMP_CLIENT, "input")
            if not amp_inputs:
                raise ReampError("can't find the amp's input in JACK — is guitarix running?")

            # what feeds the amp now -- your guitar -- so it goes back exactly
            saved = {port: [src for src in jackutil.connections(port)] for port in amp_inputs}
            for port, sources in saved.items():
                for src in sources:
                    jackutil.disconnect(src, port)
            session = {"take": take, "mode": mode, "record": bool(record),
                       "output": None, "saved": saved, "amp_inputs": amp_inputs}
            self._session = session

        try:
            self.player.play(path, self._targets(mode), loop=bool(loop) and not record)
            if record:
                with self._lock:
                    if self._session is not session or session.get("finishing"):
                        raise ReampError("playback ended before recording started")
                    status = self.rec.start(name=name or self._render_name(take))
                    if status.get("error"):
                        raise ReampError(status["error"])
                    session["output"] = status.get("file")
        except (PlayerError, ReampError, OSError) as exc:
            self.player.stop()
            self._finish()
            raise ReampError(str(exc))
        self.on_change()
        return session["output"] if record else None

    def set_mode(self, mode):
        """Amp on ("wet") or off ("dry"), without stopping playback."""
        s = self._session
        if not s or mode == s["mode"]:
            return
        if s["record"]:
            # the recorder listens to the amp's output; with the amp bypassed
            # it would be capturing silence
            raise ReampError("the amp can't be switched off while a pass is being recorded")
        s["mode"] = mode
        self.player.route(self._targets(mode))
        self.on_change()

    def pause(self, paused=True):
        if self._session and not self._session["record"]:
            self.player.pause(paused)

    def seek(self, seconds):
        if self._session and not self._session["record"]:
            self.player.seek(seconds)

    def set_loop(self, loop):
        if self._session and not self._session["record"]:
            self.player.set_loop(loop)

    def stop(self):
        """Stop early. The player ending runs _ended, which tidies everything up."""
        if self._session:
            self.player.stop()

    # ------------------------------------------------------------ internals

    def _targets(self, mode):
        if mode == "dry":
            return jackutil.ports(OUTPUT_CLIENT, "input", FALLBACK_OUTPUTS)
        return self._session["amp_inputs"]

    def _render_name(self, take):
        stem = take.rsplit(".", 1)[0]
        return stem + " (render)"

    def _ended(self, reason):
        self._finish(reason)

    def _finish(self, reason=None):
        """
        Close the recording and reconnect the guitar, THEN report inactive.
        Anything waiting for the reamp to end -- an export about to convert
        or delete the file -- must not see it end while the recorder is still
        writing. Stopping and the file ending can both arrive; only the first
        does the work.
        """
        with self._lock:
            s = self._session
            if not s or s.get("finishing"):
                return
            s["finishing"] = True
        failed = []
        try:
            if s["record"] and self.rec.recording:
                self.rec.stop()
            # put the guitar back, whatever happened -- one failed connection
            # must not stop the others being tried
            for port, sources in s["saved"].items():
                for src in sources:
                    try:
                        ok = jackutil.connect(src, port)
                    except Exception as exc:
                        log.warning("reamp: reconnecting %s -> %s raised: %s", src, port, exc)
                        ok = False
                    if not ok:
                        failed.append("%s -> %s" % (src, port))
        finally:
            with self._lock:
                self._session = None
        if failed:
            self.restore_error = "couldn't reconnect: " + ", ".join(failed)
            log.error("reamp of %s: %s", s["take"], self.restore_error)
        else:
            self.restore_error = None
        if reason and reason.startswith("failed"):
            log.warning("reamp of %s ended: %s", s["take"], reason)
        self.on_change()
