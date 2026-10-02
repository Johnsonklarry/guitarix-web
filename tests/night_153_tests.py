#!/usr/bin/env python3
"""
Regression test for: Each failed IPC connection attempt leaks its newly created
UNIX socket file descriptor.

When _Ipc.__init__ fails to connect to the mpv control socket, the socket
object must be closed before the next retry iteration.
"""

import socket
import sys
import unittest
from unittest import mock

# Import the module under test
sys.path.insert(0, ".")
from player import _Ipc, PlayerError


class TestIpcSocketLeak(unittest.TestCase):
    """Test that failed connection attempts don't leak socket file descriptors."""

    def test_failed_connect_closes_socket(self):
        """Each failed connect() call must close its socket before retrying."""
        # Create a mock socket that will fail on connect
        mock_sock = mock.MagicMock(spec=socket.socket)
        mock_sock.connect.side_effect = OSError("Connection refused")

        with mock.patch("player.socket.socket", return_value=mock_sock) as mock_socket_class:
            # The _Ipc constructor should retry several times before giving up
            with self.assertRaises(PlayerError):
                _Ipc("/nonexistent/socket", timeout=0.2)

            # Verify that a new socket was created for each attempt
            self.assertGreater(mock_socket_class.call_count, 1,
                               "Expected multiple connection attempts")

            # Verify that close() was called on each failed socket
            self.assertEqual(mock_sock.close.call_count, mock_socket_class.call_count,
                             "Each created socket must be closed on connection failure")

    def test_successful_connect_does_not_close_socket(self):
        """A successful connect must not close the socket."""
        mock_sock = mock.MagicMock(spec=socket.socket)
        mock_sock.connect.return_value = None  # Success

        with mock.patch("player.socket.socket", return_value=mock_sock):
            ipc = _Ipc("/fake/socket", timeout=0.2)
            # The socket should be kept open for use
            mock_sock.close.assert_not_called()
            ipc.close()  # Clean up

    def test_multiple_failures_then_success(self):
        """If first attempts fail but later one succeeds, failed sockets are closed."""
        # First two sockets fail, third succeeds
        fail_sock1 = mock.MagicMock(spec=socket.socket)
        fail_sock1.connect.side_effect = OSError("Connection refused")
        fail_sock2 = mock.MagicMock(spec=socket.socket)
        fail_sock2.connect.side_effect = OSError("Connection refused")
        success_sock = mock.MagicMock(spec=socket.socket)
        success_sock.connect.return_value = None

        sockets = [fail_sock1, fail_sock2, success_sock]
        with mock.patch("player.socket.socket", side_effect=sockets):
            ipc = _Ipc("/fake/socket", timeout=0.5)
            # Both failed sockets should have been closed
            fail_sock1.close.assert_called_once()
            fail_sock2.close.assert_called_once()
            # Successful socket should not be closed (until explicit close)
            success_sock.close.assert_not_called()
            ipc.close()


if __name__ == "__main__":
    unittest.main()
