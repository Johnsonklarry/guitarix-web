import threading
import unittest
from app import StateCoordinator


class TestStateCoordinator(unittest.TestCase):
    def test_initial_state(self):
        coord = StateCoordinator()
        self.assertEqual(coord.get_generation(), 0)
        self.assertEqual(coord.snapshot(), {"generation": 0, "states": {}})

    def test_monotonic_generation_and_snapshot(self):
        coord = StateCoordinator()
        gen1 = coord.update_subsystem("amp", {"volume": 5})
        self.assertEqual(gen1, 1)
        self.assertEqual(coord.get_generation(), 1)
        snap = coord.snapshot()
        self.assertEqual(snap["generation"], 1)
        self.assertEqual(snap["states"], {"amp": {"volume": 5}})

    def test_concurrent_updates(self):
        coord = StateCoordinator()
        num_threads = 10
        updates_per_thread = 50

        def worker(thread_id):
            for i in range(updates_per_thread):
                coord.update_subsystem(f"sub_{thread_id}", i)
                snap = coord.snapshot()
                self.assertGreaterEqual(snap["generation"], coord.get_generation() - updates_per_thread * num_threads)
                self.assertIn(f"sub_{thread_id}", snap["states"])

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(coord.get_generation(), num_threads * updates_per_thread)
        final_snap = coord.snapshot()
        self.assertEqual(final_snap["generation"], num_threads * updates_per_thread)
        self.assertEqual(len(final_snap["states"]), num_threads)


if __name__ == "__main__":
    unittest.main()
