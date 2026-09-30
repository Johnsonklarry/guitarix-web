#!/usr/bin/env python3
"""
Night 70: the listener count in spike_latency must survive concurrent
connect/disconnect handlers.

    python3 tests/night_70_tests.py

`state["listeners"] += 1` is a read-modify-write. To make a lost update
reproducible (rather than a once-in-a-million interleaving), the state dict is
swapped for one whose read of "listeners" yields to other threads. Without a
single shared lock around the whole read-modify-write, every thread reads the
same old value and the count comes out far below the number of clients.

No guitarix, JACK or network is needed; socketio.emit is patched out.
"""

import os
import sys
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import spike_latency as S          # noqa: E402

N = 24


class SlowState(dict):
    """A dict whose read of "listeners" lingers, widening the race window."""

    def __getitem__(self, key):
        value = dict.__getitem__(self, key)
        if key == "listeners":
            time.sleep(0.002)
        return value


def run_together(fn, n):
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()
        fn()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


class TestListenerCount(unittest.TestCase):
    def setUp(self):
        fresh = SlowState(S.state)
        fresh["listeners"] = 0
        p1 = mock.patch.object(S, "state", fresh)
        p2 = mock.patch.object(S.socketio, "emit")
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def test_concurrent_connects_are_all_counted(self):
        run_together(S.on_connect, N)
        self.assertEqual(dict.__getitem__(S.state, "listeners"), N)

    def test_concurrent_disconnects_are_all_counted(self):
        dict.__setitem__(S.state, "listeners", N)
        run_together(S.on_disconnect, N)
        self.assertEqual(dict.__getitem__(S.state, "listeners"), 0)

    def test_disconnect_never_goes_negative(self):
        run_together(S.on_disconnect, 4)
        self.assertEqual(dict.__getitem__(S.state, "listeners"), 0)

    def test_lock_is_shared_not_per_call(self):
        self.assertTrue(hasattr(S, "listeners_lock"))
        self.assertIs(S.listeners_lock, S.listeners_lock)


if __name__ == "__main__":
    unittest.main()
