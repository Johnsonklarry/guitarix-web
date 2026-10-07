import unittest
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(HERE, "stubs"), ROOT]

import app as A

class TestAuthGuard(unittest.TestCase):
    def test_auth_guard_registration(self):
        # Verify guard is in handlers
        self.assertIn("connect", A.socketio.handlers)
        # Verify guard refuses connection when no session is provided
        self.assertFalse(A.socketio.handlers["connect"]())

if __name__ == "__main__":
    unittest.main()
