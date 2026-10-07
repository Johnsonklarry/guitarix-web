import threading
import time
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
        # an explicit small capacity, so the test exercises eviction at its own
        # bound instead of depending on the module-level default
        small = app._OpJournal(capacity=3, clock=lambda: self.now)

        with mock.patch.object(app, "_op_journal", small):
            for i in range(3):
                small.put("old-%d" % i, (i, True))

            self.assertIsNotNone(small.get("old-0"))
            self.assertEqual(3, len(small))

            app.client_rec_delete_many({"op": 1, "op_id": "newest", "names": ["z"]})

            self.assertEqual(3, len(small))
            self.assertIsNone(small.get("old-0"))
            self.assertIsNotNone(small.get("old-1"))
            self.assertIsNotNone(small.get("old-2"))
            self.assertIsNotNone(small.get("newest"))

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

    def test_unhashable_operation_id_is_processed_untracked(self):
        msg = {"op": 4, "op_id": ["not", "hashable"], "names": ["w"]}

        app.client_rec_delete_many(msg)
        app.client_rec_delete_many(msg)

        # an id that can't be a dict key is no id at all: both requests run and
        # nothing is retained for it, exactly like an omitted op_id
        self.assertEqual([["w"], ["w"]], self.rec.calls)
        self.assertEqual([(4, True), (4, True)], self.done_calls)
        self.assertEqual(0, len(self.journal))

    def test_concurrent_duplicate_retry_deletes_only_once(self):
        started = threading.Event()
        release = threading.Event()
        calls = []
        self.addCleanup(release.set)

        class BlockingRecorder:
            """Holds the first deletion open so a retry has to queue behind it."""

            def delete_many(self, names):
                calls.append(list(names))
                started.set()
                release.wait(5)
                return (list(names), [])

        msg = {"op": 11, "op_id": "concurrent", "names": ["a"]}
        results = []

        with mock.patch.object(app, "rec", BlockingRecorder()):
            first = threading.Thread(
                target=lambda: results.append(app.client_rec_delete_many(msg)))
            first.start()
            self.assertTrue(started.wait(5), "the first deletion never started")

            second = threading.Thread(
                target=lambda: results.append(app.client_rec_delete_many(msg)))
            second.start()
            time.sleep(0.2)          # let the retry reach the check
            release.set()
            first.join(5)
            second.join(5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        # the retry replayed the journal instead of deleting a second time
        self.assertEqual([["a"]], calls)
        self.assertEqual(2, len(results))


if __name__ == "__main__":
    unittest.main()
