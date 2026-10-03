"""Regression tests for the priority-lane queue in gx_rpc.py (issue #127 part 1).

Standalone: python3 tests/night_222_tests.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gx_rpc import PriorityLaneQueue


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, dt):
        self.now += dt


class PriorityLaneTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.q = PriorityLaneQueue(high_deadline=0.05, low_deadline=0.5,
                                   clock=self.clock)

    def test_high_before_low(self):
        self.q.enqueue("low-1", PriorityLaneQueue.LOW)
        self.q.enqueue("high-1", PriorityLaneQueue.HIGH)
        self.q.enqueue("low-2", PriorityLaneQueue.LOW)
        self.q.enqueue("high-2", PriorityLaneQueue.HIGH)

        order = [self.q.next_eligible()["op"] for _ in range(4)]
        self.assertEqual(order, ["high-1", "high-2", "low-1", "low-2"])

    def test_deadline_metadata_retained(self):
        hid = self.q.enqueue("h", PriorityLaneQueue.HIGH)
        lid = self.q.enqueue("l", PriorityLaneQueue.LOW)
        self.assertNotEqual(hid, lid)

        high = self.q.next_eligible()
        self.assertEqual(high["op"], "h")
        self.assertEqual(high["priority"], PriorityLaneQueue.HIGH)
        self.assertEqual(high["deadline"], 0.05)
        self.assertEqual(high["enqueued_at"], 0.0)

        low = self.q.next_eligible()
        self.assertEqual(low["op"], "l")
        self.assertEqual(low["priority"], PriorityLaneQueue.LOW)
        self.assertEqual(low["deadline"], 0.5)

    def test_cancelled_items_skipped(self):
        self.q.enqueue("low-1", PriorityLaneQueue.LOW)
        hid = self.q.enqueue("high-1", PriorityLaneQueue.HIGH)
        self.q.enqueue("high-2", PriorityLaneQueue.HIGH)

        self.assertTrue(self.q.cancel(hid))
        self.assertTrue(self.q.is_cancelled(hid))

        order = [self.q.next_eligible()["op"] for _ in range(2)]
        self.assertEqual(order, ["high-2", "low-1"])
        self.assertIsNone(self.q.next_eligible())

    def test_expired_items_skipped(self):
        self.q.enqueue("high-1", PriorityLaneQueue.HIGH)
        self.clock.advance(0.06)          # past the 50ms high deadline
        self.q.enqueue("low-1", PriorityLaneQueue.LOW)

        entry = self.q.next_eligible()
        self.assertEqual(entry["op"], "low-1")

    def test_empty_queue_returns_none(self):
        self.assertIsNone(self.q.next_eligible())

    def test_unknown_priority_rejected(self):
        with self.assertRaises(ValueError):
            self.q.enqueue("x", "medium")


if __name__ == "__main__":
    unittest.main()
