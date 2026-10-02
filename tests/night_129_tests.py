#!/usr/bin/env python3
"""
Night 129: generation-aware state coordinator tests.
Verifies thread-safe monotonic generation assignment and consistent snapshot views.

    python3 tests/night_129_tests.py
"""

import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(HERE, "stubs"), ROOT]

import app


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = app.StateCoordinator(app.state)

    def test_monotonic_generation(self):
        g1 = self.coordinator.next_generation()
        g2 = self.coordinator.next_generation()
        self.assertGreater(g2, g1)

    def test_snapshot_includes_generation(self):
        gen = self.coordinator.next_generation()
        snap = self.coordinator.snapshot()
        self.assertIn("generation", snap)
        self.assertEqual(snap["generation"], self.coordinator.generation)

    def test_publish_attaches_generation(self):
        emitted = []
        original_emit = app.socketio.emit
        app.socketio.emit = lambda event, data=None, *a, **kw: emitted.append((event, data))
        try:
            gen = self.coordinator.publish("status", {"connected": True})
            self.assertTrue(len(emitted) > 0)
            event, data = emitted[-1]
            self.assertEqual(event, "status")
            self.assertEqual(data.get("generation"), gen)
        finally:
            app.socketio.emit = original_emit

    def test_concurrent_generation_increment(self):
        threads = []
        iterations = 100
        gens = []
        lock = threading.Lock()

        def worker():
            for _ in range(iterations):
                g = self.coordinator.next_generation()
                with lock:
                    gens.append(g)

        for _ in range(10):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(gens), len(set(gens)))


if __name__ == "__main__":
    unittest.main()
