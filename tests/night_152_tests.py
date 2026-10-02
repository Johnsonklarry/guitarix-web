import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import player


class TestConcurrentPlay(unittest.TestCase):
    def test_concurrent_play_serialized(self):
        p = player.Player("test-client")
        active_calls = 0
        max_active = 0
        call_lock = threading.Lock()

        def fake_stop():
            pass

        def fake_installed():
            return True

        def fake_popen(*args, **kwargs):
            nonlocal active_calls, max_active
            with call_lock:
                active_calls += 1
                if active_calls > max_active:
                    max_active = active_calls
            time.sleep(0.05)
            with call_lock:
                active_calls -= 1
            proc = mock.MagicMock()
            proc.poll.return_value = 0
            proc.communicate.return_value = (b"", b"")
            proc.returncode = 0
            return proc

        with mock.patch.object(p, "stop", side_effect=fake_stop), \
             mock.patch.object(player.Player, "installed", side_effect=fake_installed), \
             mock.patch("subprocess.Popen", side_effect=fake_popen), \
             mock.patch("player._Ipc", return_value=mock.MagicMock()), \
             mock.patch.object(p, "_wait_for_ports", return_value=["test:out"]), \
             mock.patch.object(p, "_wire", return_value=None):

            t1 = threading.Thread(target=p.play, args=("song1.wav", ["sys:in"]))
            t2 = threading.Thread(target=p.play, args=("song2.wav", ["sys:in"]))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        self.assertEqual(max_active, 1, "Concurrent play() calls executed in parallel without serialization")


if __name__ == "__main__":
    unittest.main()
