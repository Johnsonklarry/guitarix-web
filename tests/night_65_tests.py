#!/usr/bin/env python3
"""
Regression test for the public_audition() locking issue.

    python3 tests/night_65_tests.py

state.audition is written under state.lock, so every reader of
public_audition() must hold that lock. state.lock is a plain Lock (not
reentrant), so public_audition() must not take it itself. This checks that
snapshot() calls it with the lock held, and that it copies only public fields.
"""

import os
import socket
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


os.environ["GX_PORT"] = str(_free_port())          # nothing is listening there
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


class PublicAuditionLockTest(unittest.TestCase):
    def setUp(self):
        self.saved = A.state.audition

    def tearDown(self):
        A.state.audition = self.saved
        A.state.__dict__.pop("public_audition", None)

    def test_snapshot_reads_audition_under_the_lock(self):
        seen = []
        real = A.AmpState.public_audition

        def spy():
            seen.append(A.state.lock.locked())
            return real(A.state)

        A.state.public_audition = spy
        A.state.audition = {"name": "x", "bank": "b", "before": {}, "prev": None}
        snap = A.state.snapshot()
        self.assertEqual(seen, [True], "snapshot must call public_audition holding state.lock")
        self.assertEqual(snap["audition"]["name"], "x")

    def test_public_fields_only(self):
        A.state.audition = {"name": "x", "bank": "b", "replaces": True,
                            "base": "b/y", "before": {"a": 1}, "prev": ("b", "z")}
        with A.state.lock:
            pub = A.state.public_audition()
        self.assertEqual(pub, {"name": "x", "bank": "b", "replaces": True, "base": "b/y"})

    def test_none_when_not_auditioning(self):
        A.state.audition = None
        with A.state.lock:
            self.assertIsNone(A.state.public_audition())


if __name__ == "__main__":
    unittest.main()
