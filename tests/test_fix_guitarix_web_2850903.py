import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app


class OpAlreadyDoneTest(unittest.TestCase):
    def setUp(self):
        self.events = []

        def capture(event, data=None, *a, **kw):
            self.events.append((event, data))

        self._emit_patch = mock.patch.object(app.socketio, "emit", side_effect=capture)
        self._emit_patch.start()
        self.addCleanup(self._emit_patch.stop)

    def _op_done_events(self):
        return [d for (e, d) in self.events if e == "op_done"]

    def test_known_op_id_skips_delete_and_replays(self):
        app.done_journal_record("op-x", "rec_delete", True)
        with mock.patch.object(app.rec, "delete", side_effect=AssertionError("rec.delete must not run")) as delete:
            app.client_rec_delete({"op": "op-x", "name": "take.wav"})
        self.assertEqual(delete.call_count, 0)
        done_events = self._op_done_events()
        self.assertEqual(len(done_events), 1)
        self.assertTrue(done_events[0]["ok"])

    def test_unknown_op_id_runs_delete_once(self):
        with mock.patch.object(app.rec, "delete", return_value=None) as delete:
            app.client_rec_delete({"op": "op-fresh", "name": "take.wav"})
        self.assertEqual(delete.call_count, 1)
        done_events = self._op_done_events()
        self.assertEqual(len(done_events), 1)
        self.assertTrue(done_events[0]["ok"])


if __name__ == "__main__":
    unittest.main()
