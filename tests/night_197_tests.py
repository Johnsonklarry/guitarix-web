#!/usr/bin/env python3
import threading
import unittest
from unittest.mock import MagicMock
import player


class TestPlayerLockAndStop(unittest.TestCase):
    def test_lock_is_rlock_and_stop_reentrant(self):
        p = player.Player("test")
        self.assertIsInstance(p._lock, type(threading.RLock()))
        # Calling stop while holding the lock should not deadlock
        with p._lock:
            p.stop()

    def test_stop_body_wrapped_in_lock(self):
        p = player.Player("test")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        lock_held_during_wait = False

        def wait_side_effect(*args, **kwargs):
            nonlocal lock_held_during_wait
            # Check if lock is acquired by the current thread
            # RLock._is_owned() returns True if held by current thread
            lock_held_during_wait = p._lock._is_owned()

        mock_proc.wait.side_effect = wait_side_effect
        p._proc = mock_proc
        p._ipc = MagicMock()

        p.stop()
        self.assertTrue(lock_held_during_wait, "Player.stop() did not hold self._lock during teardown")


if __name__ == "__main__":
    unittest.main()
