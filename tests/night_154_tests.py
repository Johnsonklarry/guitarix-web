#!/usr/bin/env python3
"""
Regression test for stderr=PIPE deadlock issue in player.py.
"""

import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from player import Player, PlayerError

class TestPlayerStderrDeadlock(unittest.TestCase):
    def setUp(self):
        self.player = Player("test_client")
        self.test_file = os.path.join(tempfile.gettempdir(), "test.wav")
        with open(self.test_file, "wb") as f:
            f.write(b"\0" * 1024)  # Create a small test file

    def tearDown(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    @patch('player.subprocess.Popen')
    def test_stderr_pipe_deadlock(self, mock_popen):
        # Mock the subprocess.Popen to simulate a process that writes to stderr
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.communicate.return_value = (None, b"a" * 1024 * 1024)  # Simulate large stderr output

        mock_popen.return_value = mock_proc

        # Test that the player can handle large stderr output without deadlocking
        try:
            self.player.play(self.test_file, ["test_target"], volume=50, loop=False)
            self.player.stop()
        except PlayerError as e:
            self.fail(f"PlayerError raised unexpectedly: {e}")

        # Verify that the process was properly stopped
        mock_proc.kill.assert_called_once()

if __name__ == '__main__':
    unittest.main()
