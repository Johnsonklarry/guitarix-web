import queue
import unittest

import monitor


class TestAdaptiveShedding(unittest.TestCase):
    def setUp(self):
        monitor.report_buffer_time(None)

    def tearDown(self):
        monitor.report_buffer_time(None)

    def test_default_budget_when_no_report(self):
        self.assertEqual(monitor.effective_budget(), monitor.DEFAULT_LAG_BUDGET)

    def test_effective_budget_clamps(self):
        monitor.report_buffer_time(0.05)
        self.assertEqual(monitor.effective_budget(), monitor.MIN_LAG_BUDGET)

        monitor.report_buffer_time(5.0)
        self.assertEqual(monitor.effective_budget(), monitor.MAX_LAG_BUDGET)

        monitor.report_buffer_time(0.8)
        self.assertAlmostEqual(monitor.effective_budget(), 0.8)

    def test_offer_sheds_based_on_effective_budget(self):
        q = queue.Queue()
        # With default budget (0.5s * 20000 = 10000 bytes)
        chunk = b"x" * 4000
        monitor._offer(q, chunk)
        monitor._offer(q, chunk)
        self.assertEqual(q.qsize(), 2)  # 8000 bytes queued <= 10000

        # Reporting a lower buffer time (clamped to 0.2s * 20000 = 4000 bytes)
        monitor.report_buffer_time(0.2)
        # Adding a new chunk of 2000 bytes exceeds 4000 total (8000+2000), sheds old chunks
        monitor._offer(q, b"y" * 2000)
        remaining = list(q.queue)
        total_queued = sum(len(c) for c in remaining)
        self.assertLessEqual(total_queued, int(0.2 * monitor.BYTES_PER_SEC))
        self.assertEqual(remaining[-1], b"y" * 2000)


if __name__ == "__main__":
    unittest.main()
