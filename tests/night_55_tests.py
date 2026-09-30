#!/usr/bin/env python3
"""
Regression test (night 55): two concurrent import_audition operations.

    python3 tests/night_55_tests.py

The freshness check (state.audition is None), the snapshot of what was
playing, and the install of state.audition must be atomic across operations.
Without that, the second operation snapshots settings the first one already
changed, and Discard would restore the wrong sound.

The engine is faked at the app's own seams; nothing here needs guitarix.
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

os.environ.setdefault("GX_PORT", "1")            # nothing is listening there
os.environ["FAKE_JACK_DIR"] = tempfile.mkdtemp(prefix="gxweb-jack-")
os.environ["PATH"] = os.path.join(HERE, "fakes", "bin") + os.pathsep + os.environ["PATH"]
sys.path[:0] = [os.path.join(HERE, "stubs"), ROOT]

import logging                       # noqa: E402
logging.disable(logging.WARNING)

import recorder                      # noqa: E402
import backing as backing_mod        # noqa: E402
recorder.RECORDINGS_DIR = tempfile.mkdtemp(prefix="gxweb-rec-")
backing_mod.BACKING_DIR = tempfile.mkdtemp(prefix="gxweb-backing-")

import app as A                      # noqa: E402


class AuditionRaceTest(unittest.TestCase):
    def setUp(self):
        self.live = {"x": 0}
        self.gets = 0
        self.gets_lock = threading.Lock()
        self.first_getter = None
        self.old_audition = A.state.audition
        A.state.audition = None
        A.socketio.emitted.clear()

    def tearDown(self):
        A.state.audition = self.old_audition

    def wait_ops(self, ops, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            seen = {d["op"] for e, d in list(A.socketio.emitted) if e == "op_done"}
            if set(ops) <= seen:
                return
            time.sleep(0.05)
        self.fail("operations never finished: %s" % (list(A.socketio.emitted),))

    def test_second_audition_does_not_overwrite_the_snapshot(self):
        test = self

        class FakeRpc:
            def get(self, ids):
                with test.gets_lock:
                    test.gets += 1
                    n = test.gets
                    if n == 1:
                        test.first_getter = threading.current_thread()
                if n > 1:
                    time.sleep(0.3)      # reads after the first op has applied
                return dict(test.live)

        def prepare(msg):
            return {"name": "P%d" % msg["n"], "params": {}, "n": msg["n"]}, None, {}, [], "Bank"

        def load_and_plan(preset, base, params, others_off):
            return {"x": preset["n"]}, {"set": ["x"]}

        def apply(changes):
            self.live.update(changes)

        def banks_now():
            # the first operation is slow to install, so a second one that
            # ignored it would finish (and install) first
            if threading.current_thread() is self.first_getter:
                time.sleep(0.6)
            return {}

        with mock.patch.object(A, "rpc", FakeRpc()), \
                mock.patch.object(A, "_prepare_one", prepare), \
                mock.patch.object(A, "_loaded_now", lambda: ("B", "P")), \
                mock.patch.object(A, "_load_base_and_plan", load_and_plan), \
                mock.patch.object(A, "_apply", apply), \
                mock.patch.object(A, "_banks_now", banks_now), \
                mock.patch.object(A, "_resync", lambda: None), \
                mock.patch.object(A.presets_io, "export_ids", lambda p: []):
            handler = A.socketio.handlers["import_audition"]
            handler({"n": 1, "op": "o1"})
            handler({"n": 2, "op": "o2"})
            self.wait_ops(["o1", "o2"])

        self.assertIsNotNone(A.state.audition)
        self.assertEqual(A.state.audition["before"], {"x": 0},
                         "the snapshot must be what was playing before ANY audition")
        self.assertEqual(self.gets, 1, "only the first audition should take a snapshot")


if __name__ == "__main__":
    unittest.main()
