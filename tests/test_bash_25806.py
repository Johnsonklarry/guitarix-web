"""
Tests for the recorder's take state machine (issue #25806).

A take walks starting -> wired -> writing -> finalizing -> ready, and
listing() publishes it only once it gets there: a file that is still being
written, or one whose audio never arrived, is not a take yet. Every way it
can fall over shows through the recorder's own status() rather than as an
exception.

Nothing here touches JACK, ffmpeg, jack_connect or the user's recordings
folder: the engine (subprocess), the JACK client (jackutil) and the port
wiring are all stood in for, and takes land in a temp dir.
"""

import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

import recorder


class FakeProc:
    """The engine process as recorder.py sees it: it makes the file ffmpeg
    would make, and goes away when it is signalled -- unless the engine is
    stalled, in which case it never goes away at all."""

    def __init__(self, engine, args, **kwargs):
        self.engine = engine
        self.args = args
        self.returncode = None
        self.signals = []
        self.path = args[-1]
        self._done = threading.Event()
        engine.procs.append(self)
        if engine.create_file:
            with open(self.path, "wb") as f:
                f.write(b"RIFF....WAVEfmt ")
        if engine.crash_code is not None:
            self._exit(engine.crash_code)

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)
        self.engine.log.append("signal %s: %s" % (sig, os.path.basename(self.path)))
        if not self.engine.stall:
            self._exit(0)

    def kill(self):
        self.engine.log.append("kill: %s" % os.path.basename(self.path))
        if not self.engine.stall:
            self._exit(-signal.SIGKILL)

    def wait(self, timeout=None):
        if not self._done.wait(timeout if timeout is not None else 30):
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def communicate(self, *args, **kwargs):
        # a stalled engine hangs here until the test lets its thread go
        while not self.engine.release_event.is_set():
            if self._done.wait(0.05):
                break
        return b"", self.engine.stderr

    def _exit(self, code):
        self.returncode = code
        self._done.set()


class FakeEngine:
    """Builds fake engine processes and keeps a log of the faults injected
    into them, so a test can check the fault was observable."""

    def __init__(self, create_file=True, crash_code=None, stall=False, stderr=b""):
        self.procs = []
        self.log = []
        self.create_file = create_file
        self.crash_code = crash_code
        self.stall = stall
        self.stderr = stderr
        self.release_event = threading.Event()

    def __call__(self, args, **kwargs):
        return FakeProc(self, args, **kwargs)

    def release(self):
        self.release_event.set()


class FakeJack:
    """Stands in for jackutil: which ports a client has, when they turn up,
    and whether jack_connect accepts the patch."""

    def __init__(self, wet=("gx_head_amp:out_0", "gx_head_amp:out_1"),
                 dry=("system:capture_1", "system:capture_2"),
                 delay=0, drop=False):
        self.wet = list(wet)
        self.dry = list(dry)
        self.delay = delay
        self.drop = drop
        self.log = []
        self._seen = {}

    def ports(self, client, direction, fallback=None):
        seen = self._seen.get(client, 0) + 1
        self._seen[client] = seen
        if seen <= self.delay:
            self.log.append("delayed port: %s isn't there yet" % client)
            return []
        return list(self.wet if client == recorder.SOURCE_CLIENT else self.dry)

    def connect(self, src, dst):
        if self.drop:
            self.log.append("dropped connection: %s -> %s" % (src, dst))
            return 1
        self.log.append("connected %s -> %s" % (src, dst))
        return 0


class FakeWhich:
    """shutil.which as the recorder sees it on a box with the tools present.
    ffprobe is left out, so listing() never shells out for a duration."""

    PRESENT = ("ffmpeg", "jack_connect")

    def __call__(self, name):
        return "/usr/bin/%s" % name if name in self.PRESENT else None


class FakeResult:
    def __init__(self, returncode):
        self.returncode = returncode
        self.stdout = b""
        self.stderr = b""


class RecorderStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.jack = None

    # ------------------------------------------------------------ helpers

    def make_recorder(self, engine, jack):
        """A recorder driven by a fake engine, fake JACK and fake jack_connect."""
        self.jack = jack
        patches = [
            mock.patch.object(recorder.subprocess, "Popen", engine),
            mock.patch.object(recorder.subprocess, "run", self._run),
            mock.patch.object(recorder.shutil, "which", FakeWhich()),
            mock.patch.object(recorder, "jackutil", jack),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(engine.release)
        return recorder.Recorder(self.dir)

    def _run(self, cmd, **kwargs):
        if cmd and cmd[0] == "jack_connect":
            return FakeResult(self.jack.connect(cmd[1], cmd[2]))
        raise AssertionError("unexpected subprocess.run: %r" % (cmd,))

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def names(self, rec):
        return [item["name"] for item in rec.listing()]

    # ------------------------------------------------------------ tests

    def test_states_run_to_ready_and_only_then_is_the_take_listed(self):
        engine = FakeEngine()
        jack = FakeJack(delay=2)          # the ports take a moment to appear
        rec = self.make_recorder(engine, jack)

        seen = []
        original = rec._set_state

        def spy(state, force=False):
            seen.append(state)
            return original(state, force)

        rec._set_state = spy

        started = rec.start(name="Take 1", sidecar={"gain": 7})
        self.assertEqual(recorder.STATE_STARTING, started["state"])
        self.assertEqual(recorder.STATE_STARTING, rec.state)

        # the engine has opened a file, but the take is not playable yet
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "Take 1.wav")))
        self.assertEqual([], rec.listing())

        self.assertTrue(self.wait_for(lambda: rec.state == recorder.STATE_WRITING),
                        "take never reached writing: %s" % rec.state)
        self.assertEqual([], rec.listing())      # writing is still not ready

        stopped = rec.stop()
        self.assertEqual(recorder.STATE_READY, stopped["state"])

        items = rec.listing()
        self.assertEqual(["Take 1.wav"], [i["name"] for i in items])
        self.assertEqual("Take 1.json", items[0]["settings"])
        with open(os.path.join(self.dir, "Take 1.json")) as f:
            self.assertEqual({"gain": 7}, json.load(f))

        self.assertIn(recorder.STATE_WIRED, seen)
        self.assertIn(recorder.STATE_WRITING, seen)
        self.assertLess(seen.index(recorder.STATE_WIRED), seen.index(recorder.STATE_WRITING))
        self.assertEqual(recorder.STATE_READY, seen[-1])

    def test_a_take_that_never_wires_is_not_published(self):
        engine = FakeEngine()
        jack = FakeJack(drop=True)        # jack_connect refuses every pair
        rec = self.make_recorder(engine, jack)

        with mock.patch.object(recorder, "WIRING_TIMEOUT", 0.3):
            started = rec.start(name="Dropped")
            self.assertEqual(recorder.STATE_STARTING, started["state"])
            self.assertTrue(self.wait_for(lambda: rec.state == recorder.STATE_ERROR),
                            "take never failed: %s" % rec.state)

            self.assertTrue(os.path.isfile(os.path.join(self.dir, "Dropped.wav")))
            self.assertEqual([], rec.listing())
            status = rec.status()
            self.assertEqual(recorder.STATE_ERROR, status["state"])
            self.assertIn("check jack_lsp", status["error"] or "")
            self.assertIn("dropped connection", " ".join(jack.log))

    def test_a_stalled_finalise_returns_and_keeps_the_take_back(self):
        engine = FakeEngine(stall=True)
        jack = FakeJack()
        rec = self.make_recorder(engine, jack)

        with mock.patch.object(recorder, "FINALIZE_TIMEOUT", 0.2), \
                mock.patch.object(recorder, "KILL_TIMEOUT", 0.2):
            rec.start(name="Stalled")
            self.assertTrue(self.wait_for(lambda: rec.state == recorder.STATE_WRITING),
                            "take never reached writing: %s" % rec.state)
            began = time.time()
            status = rec.stop()
            elapsed = time.time() - began

        self.assertLess(elapsed, 2.0)
        self.assertNotEqual(recorder.STATE_READY, status["state"])
        self.assertIn(status["state"], (recorder.STATE_ERROR, recorder.STATE_FINALIZING))
        self.assertTrue(engine.log)       # the stall and the kill were seen
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "Stalled.wav")))
        self.assertEqual([], rec.listing())

    def test_a_crashed_engine_leaves_no_published_take(self):
        engine = FakeEngine(crash_code=1, stderr=b"jack: the amp vanished")
        jack = FakeJack()
        rec = self.make_recorder(engine, jack)

        started = rec.start(name="Crashed")
        self.assertEqual(recorder.STATE_STARTING, started["state"])
        self.assertTrue(self.wait_for(lambda: rec.state == recorder.STATE_ERROR),
                        "take never failed: %s" % rec.state)

        self.assertNotEqual(recorder.STATE_READY, rec.state)
        self.assertEqual([], rec.listing())
        self.assertIn("vanished", rec.status()["error"] or "")
        self.assertFalse(os.path.isfile(os.path.join(self.dir, "Crashed.json")))

    def test_completed_takes_survive_a_restart(self):
        engine = FakeEngine()
        jack = FakeJack()
        rec = self.make_recorder(engine, jack)

        rec.start(name="Kept")
        self.assertTrue(self.wait_for(lambda: rec.state == recorder.STATE_WRITING),
                        "take never reached writing: %s" % rec.state)
        rec.stop()
        self.assertEqual(recorder.STATE_READY, rec.state)

        # a page refresh: a new recorder over the same folder still sees it
        again = recorder.Recorder(self.dir)
        self.assertIn("Kept.wav", self.names(again))

        # and a take that is still starting is not published by the new one
        engine2 = FakeEngine()
        jack2 = FakeJack(delay=100)
        self.addCleanup(engine2.release)
        with mock.patch.object(recorder.subprocess, "Popen", engine2), \
                mock.patch.object(recorder, "jackutil", jack2):
            again.start(name="In flight")
            self.assertEqual(recorder.STATE_STARTING, again.state)
            self.assertIn("Kept.wav", self.names(again))
            self.assertNotIn("In flight.wav", self.names(again))


if __name__ == "__main__":
    unittest.main()
