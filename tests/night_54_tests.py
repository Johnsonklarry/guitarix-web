#!/usr/bin/env python3
"""
Regression test (night 54): op_done goes to the client that started the
operation, not to every connected browser.

    python3 tests/night_54_tests.py

The engine is faked at the app's own seams; nothing here needs guitarix.
"""

import os
import sys
import shutil
import atexit
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

os.environ.setdefault("GX_PORT", "1")            # nothing is listening there
os.environ.pop("GX_BROADCAST", None)
os.environ["FAKE_JACK_DIR"] = tempfile.mkdtemp(prefix="gxweb-jack-")
os.environ["PATH"] = os.path.join(HERE, "fakes", "bin") + os.pathsep + os.environ["PATH"]
sys.path[:0] = [os.path.join(HERE, "stubs"), ROOT]

import logging                       # noqa: E402
logging.disable(logging.WARNING)

import recorder                      # noqa: E402
import backing as backing_mod        # noqa: E402
recorder.RECORDINGS_DIR = tempfile.mkdtemp(prefix="gxweb-rec-")
atexit.register(shutil.rmtree, recorder.RECORDINGS_DIR, True)   # recordings are GBs: never leave them in TMPDIR
backing_mod.BACKING_DIR = tempfile.mkdtemp(prefix="gxweb-backing-")

import app as A                      # noqa: E402


class OpDoneTargetTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.lock = threading.Lock()

        def record(event, data=None, *a, **kw):
            with self.lock:
                self.sent.append((event, data, kw))

        patches = [mock.patch.object(A, "_socketio_emit", record),
                   mock.patch.object(A, "toast", lambda *a, **k: None),
                   mock.patch.object(A, "refresh_banks", lambda: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def op_done(self):
        with self.lock:
            return [(d["op"], d["ok"], kw) for e, d, kw in self.sent if e == "op_done"]

    def wait_ops(self, count, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if len(self.op_done()) >= count:
                return
            time.sleep(0.02)
        self.fail("operations never finished: %s" % (self.sent,))

    def test_synchronous_done_targets_the_requesting_client(self):
        with A.app.test_request_context():
            A.request.sid = "browser-one"
            A.done("invalid", False)
        self.assertEqual(self.op_done(), [("invalid", False, {"to": "browser-one"})])

    def test_background_done_targets_the_client_that_started_it(self):
        for sid, op in (("browser-one", "one"), ("browser-two", "two")):
            with A.app.test_request_context():
                A.request.sid = sid
                A._preset_action(lambda: None, "Saved", op=op)
        self.wait_ops(2)
        self.assertEqual(sorted(self.op_done()),
                         [("one", True, {"to": "browser-one"}),
                          ("two", True, {"to": "browser-two"})])

    def test_background_failure_targets_the_client_that_started_it(self):
        def fail():
            raise OSError("engine unavailable")

        with A.app.test_request_context():
            A.request.sid = "browser-one"
            A._preset_action(fail, "Saved", op="bad")
        self.wait_ops(1)
        self.assertEqual(self.op_done(), [("bad", False, {"to": "browser-one"})])


if __name__ == "__main__":
    unittest.main()
