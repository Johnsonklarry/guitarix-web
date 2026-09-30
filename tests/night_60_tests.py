#!/usr/bin/env python3
"""
Regression test: a failed "set" notification must not touch the local cache.

    python3 tests/night_60_tests.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import gx_rpc  # noqa: E402


class FakeSock:
    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)


class FailingSock:
    def sendall(self, data):
        raise OSError("broken pipe")


class SetCacheTests(unittest.TestCase):
    def test_failed_send_leaves_cache_untouched(self):
        rpc = gx_rpc.GuitarixRPC()
        rpc.values["a"] = 1
        rpc._sock = FailingSock()
        with self.assertRaises(OSError):
            rpc.set({"a": 2, "b": 3})
        self.assertEqual(rpc.values, {"a": 1})

    def test_not_connected_leaves_cache_untouched(self):
        rpc = gx_rpc.GuitarixRPC()
        rpc.values["a"] = 1
        with self.assertRaises(OSError):
            rpc.set({"a": 2})
        self.assertEqual(rpc.values, {"a": 1})

    def test_successful_send_updates_cache(self):
        rpc = gx_rpc.GuitarixRPC()
        sock = FakeSock()
        rpc._sock = sock
        rpc.set({"a": 2, "b": 3})
        self.assertEqual(rpc.values, {"a": 2, "b": 3})
        self.assertEqual(len(sock.sent), 1)


if __name__ == "__main__":
    unittest.main()
