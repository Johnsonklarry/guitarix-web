#!/usr/bin/env python3
"""
Night 68: concurrent _ensure_bank calls for the same new bank create it once.

    python3 tests/night_68_tests.py

Without the lock around check-then-create, every racing thread sees the bank
missing and calls bank_create; with it, exactly one does. No guitarix or JACK
needed: the engine is a fake that records bank_create calls.
"""

import os
import socket
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# app.py reads these at import time (same setup as broadcast_tests.py)
os.environ["GX_BROADCAST"] = "1"
os.environ["GX_PORT"] = str(free_port())
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


class FakeRpc:
    def __init__(self):
        self.banks_now = set()
        self.created = []
        self.guard = threading.Lock()

    def banks(self):
        with self.guard:
            return [{"name": b, "presets": []} for b in self.banks_now]

    def bank_create(self, name):
        time.sleep(0.05)             # widen the window between check and create
        with self.guard:
            self.created.append(name)
            self.banks_now.add(name)


class ConcurrentBankCreation(unittest.TestCase):
    def setUp(self):
        self.real_rpc = A.rpc
        self.real_methods = dict(A.gx_rpc.PRESET_METHODS)
        A.rpc = FakeRpc()
        A.gx_rpc.PRESET_METHODS["new_bank"] = "fake_new_bank"

    def tearDown(self):
        A.rpc = self.real_rpc
        A.gx_rpc.PRESET_METHODS.clear()
        A.gx_rpc.PRESET_METHODS.update(self.real_methods)

    def test_racing_imports_create_the_bank_once(self):
        errors = []

        def go():
            try:
                A._ensure_bank("Night Bank")
            except Exception as exc:     # surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(A.rpc.created, ["Night Bank"])


if __name__ == "__main__":
    unittest.main()
