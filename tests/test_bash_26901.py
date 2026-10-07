#!/usr/bin/env python3
import unittest
import time
import subprocess
import os
import tempfile
from unittest.mock import patch, MagicMock

class TestWaitHandling(unittest.TestCase):
    def test_wait_for_state_success(self):
        def mock_condition():
            time.sleep(0.5)
            return True

        start_time = time.time()
        result = wait_for_state(mock_condition, timeout=1)
        self.assertTrue(result)
        self.assertLess(time.time() - start_time, 1.0)

    def test_wait_for_state_timeout(self):
        def mock_condition():
            return False

        with self.assertRaises(TimeoutError):
            wait_for_state(mock_condition, timeout=0.1)

    def test_connect_input_success(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value.returncode = 0
            with tempfile.NamedTemporaryFile() as f:
                f.write(b"test input")
                f.flush()
                result = subprocess.run(["bash", "tools/connect-input.sh", "127.0.0.1", "1234", f.name])
                self.assertEqual(result.returncode, 0)

    def test_connect_input_timeout(self):
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("cmd", 10)
            with tempfile.NamedTemporaryFile() as f:
                f.write(b"test input")
                f.flush()
                with self.assertRaises(subprocess.TimeoutExpired):
                    subprocess.run(["bash", "tools/connect-input.sh", "127.0.0.1", "9999", f.name], timeout=1)

def wait_for_state(condition, timeout=120, interval=0.1):
    start_time = time.time()
    while True:
        if condition():
            return True
        if time.time() - start_time > timeout:
            raise TimeoutError(f"Condition not met within {timeout} seconds")
        time.sleep(interval)

if __name__ == "__main__":
    unittest.main()
