import unittest
from unittest.mock import patch
import app as A


class DoneJournalTests(unittest.TestCase):
    def setUp(self):
        with A._done_journal_lock:
            A._done_journal.clear()

    def tearDown(self):
        with A._done_journal_lock:
            A._done_journal.clear()

    def test_journal_ttl_expiration(self):
        fake_op_id = "test-op-123"
        fake_op = "preset_delete"
        fake_ok = True
        base_time = 1000.0
        ttl = getattr(A, "_DONE_JOURNAL_TTL", getattr(A, "DONE_JOURNAL_TTL", 24 * 60 * 60))

        with patch("time.time") as mock_time:
            mock_time.return_value = base_time
            A._journal_put(fake_op_id, fake_op, fake_ok)
            self.assertEqual(A.done_journal_lookup(fake_op_id), (fake_op, fake_ok))

            # Advance the clock past the TTL
            mock_time.return_value = base_time + ttl + 1.0
            self.assertIsNone(A.done_journal_lookup(fake_op_id))

    def test_journal_max_entries_cap(self):
        cap = getattr(A, "_DONE_JOURNAL_MAX", getattr(A, "DONE_JOURNAL_MAX", 10000))
        for i in range(cap + 50):
            A._journal_put(f"op-{i}", "test_op", True)
            self.assertLessEqual(len(A._done_journal), cap)
        self.assertEqual(len(A._done_journal), cap)


if __name__ == "__main__":
    unittest.main()
