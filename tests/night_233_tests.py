"""Regression tests for issue #135 part 3: the queue bound is adaptive, not a
hardcoded half-second constant, and the buffer-reporting event is documented.

Run: python3 tests/night_233_tests.py
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import monitor  # noqa: E402


class QueueLimitIsNotHardcoded(unittest.TestCase):
    def test_no_fixed_lag_budget_constant(self):
        # The literal 0.5-second module constant must be gone.
        self.assertFalse(hasattr(monitor, "LAG_BUDGET"),
                         "monitor.LAG_BUDGET is still a hardcoded module constant")
        self.assertFalse(hasattr(monitor, "MAX_LAG_BYTES"),
                         "monitor.MAX_LAG_BYTES is still a hardcoded module constant")

    def test_budget_is_configurable(self):
        m = monitor.Monitor(lambda: [], lag_budget=2.0)
        self.assertEqual(m.max_lag_bytes, int(2.0 * monitor.BYTES_PER_SEC))
        d = monitor.Monitor(lambda: [])
        self.assertEqual(d.max_lag_bytes,
                         int(monitor.DEFAULT_LAG_BUDGET * monitor.BYTES_PER_SEC))

    def test_offer_uses_the_given_bound(self):
        import queue
        q = queue.Queue()
        # A tiny bound: the first item is shed once the second arrives.
        monitor._offer(q, b"a" * 100, 150)
        monitor._offer(q, b"b" * 100, 150)
        self.assertEqual(q.qsize(), 1)
        self.assertEqual(q.get_nowait(), b"b" * 100)


class ReadmeDocumentsTheEvent(unittest.TestCase):
    def test_readme_names_the_buffer_reporting_event(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("playback_buffer", text,
                      "README.md does not document the playback buffer event")
        self.assertTrue(re.search(r"adaptive", text, re.I),
                        "README.md does not describe the adaptive shedding")


if __name__ == "__main__":
    unittest.main()
