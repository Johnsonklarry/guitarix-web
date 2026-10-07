import unittest
from unittest import mock

import app


class _Recorder:
    """Stand-in for the recording store used by app.rec."""

    def __init__(self):
        self.calls = []

    def delete_many(self, names):
        self.calls.append(list(names))
        return (list(names), [])


class RecDeleteManyJournalTest(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.journal = app._OpJournal(clock=lambda: self.now)
        self.rec = _Recorder()
        self.done_calls = []
        self.toasts = []

        def fake_done(op, ok):
            self.done_calls.append((op, ok))
            return ("result", op, ok)

        def fake_toast(*args, **kwargs):
            self.toasts.append((args, kwargs))

        patches = (
            mock.patch.object(app, "_op_journal", self.journal),
            mock.patch.object(app, "rec", self.rec),
            mock.patch.object(app, "toast", fake_toast),
            mock.patch.object(app, "done", fake_done),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_new_operation_runs_and_duplicate_replays_journal(self):
        msg = {"op": 7, "op_id": "durable-1", "names": ["a", "b"]}

        first = app.client_rec_delete_many(msg)

        self.assertEqual([["a", "b"]], self.rec.calls)
        self.assertEqual([(7, True)], self.done_calls)
        self.assertEqual(("result", 7, True), first)

        second = app.client_rec_delete_many(msg)

        self.assertEqual([["a", "b"]], self.rec.calls)
        self.assertEqual([(7, True), (7, True)], self.done_calls)
        self.assertEqual(first, second)

    def test_journal_capacity_evicts_oldest_operation_id(self):
        for i in range(10000):
            self.journal.put("old-%d" % i, (i, True))

        self.assertIsNotNone(self.journal.get("old-0"))
        self.assertEqual(10000, len(self.journal))

        app.client_rec_delete_many({"op": 1, "op_id": "newest", "names": ["z"]})

        self.assertEqual(10000, len(self.journal))
        self.assertIsNone(self.journal.get("old-0"))
        self.assertIsNotNone(self.journal.get("old-1"))
        self.assertIsNotNone(self.journal.get("newest"))

    def test_expired_operation_id_is_processed_as_new(self):
        msg = {"op": 2, "op_id": "expires", "names": ["x"]}

        app.client_rec_delete_many(msg)

        self.assertEqual(1, len(self.rec.calls))

        self.now += app._OP_JOURNAL_TTL + 1

        self.assertIsNone(self.journal.get("expires"))

        app.client_rec_delete_many(msg)

        self.assertEqual(2, len(self.rec.calls))
        self.assertEqual([(2, True), (2, True)], self.done_calls)

    def test_missing_operation_id_is_handled_without_tracking(self):
        msg = {"op": 3, "names": ["y"]}

        app.client_rec_delete_many(msg)
        app.client_rec_delete_many(msg)

        self.assertEqual(2, len(self.rec.calls))
        self.assertEqual(0, len(self.journal))
        self.assertEqual([(3, True), (3, True)], self.done_calls)


if __name__ == "__main__":
    unittest.main()
