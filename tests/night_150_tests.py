#!/usr/bin/env python3
"""
Regression test for #92: verify wait_for_state function works with polling instead of fixed waits.
"""

import unittest
import time
from unittest.mock import patch
from tests.day_gx_43_tests import wait_for_state

class TestPollingWait(unittest.TestCase):
    def test_wait_for_state(self):
        # Test that wait_for_state returns after condition is true
        condition_met = False

        def mock_condition():
            nonlocal condition_met
            return condition_met

        # Start a thread to set the condition after a short delay
        def set_condition():
            time.sleep(0.1)
            nonlocal condition_met
            condition_met = True

        import threading
        t = threading.Thread(target=set_condition)
        t.start()

        # Should return immediately after condition is met
        start_time = time.time()
        wait_for_state(mock_condition, timeout=1.0)
        elapsed = time.time() - start_time
        self.assertLess(elapsed, 0.2, "Waited too long for condition")

        # Test timeout behavior
        condition_met = False
        with self.assertRaises(TimeoutError):
            wait_for_state(mock_condition, timeout=0.1)

if __name__ == "__main__":
    unittest.main()
