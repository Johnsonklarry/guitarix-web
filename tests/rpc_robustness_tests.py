#!/usr/bin/env python3
"""
Regression tests for two small robustness bugs.

    python3 tests/rpc_robustness_tests.py

* gx_rpc._read_loop must not dereference a socket that _close() cleared.
* spike_latency's ping2 handler must survive a non-dict payload.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import gx_rpc  # noqa: E402


class ClearingSocket:
    """recv() simulates _close() racing in: it clears the owner's _sock."""

    def __init__(self, rpc, chunks):
        self.rpc = rpc
        self.chunks = list(chunks)

    def recv(self, n):
        self.rpc._sock = None
        return self.chunks.pop(0)


class ReadLoopTests(unittest.TestCase):
    def test_cleared_socket_mid_loop_is_oserror(self):
        rpc = gx_rpc.GuitarixRPC()
        rpc._sock = ClearingSocket(rpc, [b"partial-no-newline", b""])
        # Unfixed: the second iteration does None.recv -> AttributeError.
        with self.assertRaises(OSError):
            rpc._read_loop()

    def test_no_socket_is_oserror(self):
        rpc = gx_rpc.GuitarixRPC()
        with self.assertRaises(OSError):
            rpc._read_loop()


class PingTests(unittest.TestCase):
    def setUp(self):
        try:
            import spike_latency
        except SystemExit:
            self.skipTest("flask / flask-socketio not installed")
        self.on_ping = spike_latency.on_ping

    def test_dict_payload(self):
        self.assertEqual(self.on_ping({"t": 5})["client"], 5)

    def test_non_dict_payloads_do_not_raise(self):
        for bad in ("x", 3, [1, 2], True):
            self.assertIsNone(self.on_ping(bad)["client"])
        self.assertIsNone(self.on_ping(None)["client"])


if __name__ == "__main__":
    unittest.main()
