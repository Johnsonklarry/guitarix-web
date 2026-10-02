#!/usr/bin/env python3
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class TestPing2ErrorHandling(unittest.TestCase):
    def test_sync_ping2_has_timeout_and_error_handling(self):
        spike_path = os.path.join(ROOT, "spike_latency.py")
        with open(spike_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check that the ping2 call in sync() includes timeout and error handling
        self.assertIn("socket.emit('ping2'", content)
        self.assertIn("setTimeout", content, "ping2 emit callback lacks timeout handling")
        self.assertIn("clearTimeout", content, "ping2 emit callback lacks timer cleanup")
        self.assertTrue("catch" in content or "typeof r.server" in content, "ping2 callback lacks error handling")


if __name__ == "__main__":
    unittest.main()
