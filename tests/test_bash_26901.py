#!/usr/bin/env python3
"""Regression for bash-26901: tools/connect-input.sh must be driven for real.

The connect path is exercised against a real listening socket, so patching
subprocess.run cannot make a test here pass. The wait helper belongs to the
delegation API under test and is not copied into this file: a private copy
proved nothing about the shipped code.
"""
import os
import socket
import subprocess
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONNECT_INPUT = os.path.join(ROOT, "tools", "connect-input.sh")


class TestConnectInput(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(CONNECT_INPUT):
            self.skipTest("%s is missing" % CONNECT_INPUT)

    def input_file(self):
        handle = tempfile.NamedTemporaryFile()
        self.addCleanup(handle.close)
        handle.write(b"test input")
        handle.flush()
        return handle.name

    def test_connect_input_reaches_a_live_peer(self):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        accepted = []

        def accept_once():
            listener.settimeout(30)
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            accepted.append(conn)
            with conn:
                conn.settimeout(10)
                try:
                    conn.recv(65536)
                except OSError:
                    pass

        thread = threading.Thread(target=accept_once)
        thread.daemon = True
        thread.start()
        try:
            subprocess.run(["bash", CONNECT_INPUT, "127.0.0.1",
                            str(listener.getsockname()[1]), self.input_file()],
                           capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            pass    # the peer never answers; only that the tool got there matters
        thread.join(timeout=5)
        self.assertTrue(accepted,
                        "tools/connect-input.sh never connected to the peer")


if __name__ == "__main__":
    unittest.main()
