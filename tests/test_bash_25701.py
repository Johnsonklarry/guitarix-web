#!/usr/bin/env python3
"""
Regression test for issue #25751: restore_error tracking across the reamp
session lifecycle.

    python3 tests/test_bash_25701.py

Reamp._finish() puts the guitar's wiring back after a reamp session. Every
reconnect is attempted; failures (a False return or a raised exception) are
logged and reported through Reamp.restore_error. A clean reconnect sequence
must clear restore_error so a stale error from an earlier session is not
shown by the UI.

No JACK is needed: jackutil.connect is patched.
"""

import os
import sys
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import reamp  # noqa: E402


class FakeRec:
    recording = False

    def stop(self):
        pass


def make_reamp(saved):
    r = reamp.Reamp.__new__(reamp.Reamp)      # skip Player(): no JACK here
    r.rec = FakeRec()
    r.changes = 0

    def changed():
        r.changes += 1

    r.on_change = changed
    r._lock = threading.Lock()
    r.restore_error = None
    r._session = {"take": "t.wav", "mode": "wet", "record": False, "output": None,
                  "saved": saved, "amp_inputs": list(saved)}
    return r


class RestoreErrorLifecycleTests(unittest.TestCase):
    def test_clean_reconnect_clears_restore_error(self):
        r = make_reamp({"gx:in": ["system:capture_1"]})
        r.restore_error = "couldn't reconnect: stale -> gx:in"
        with mock.patch.object(reamp.jackutil, "connect", return_value=True) as c:
            r._finish()
        c.assert_called_once_with("system:capture_1", "gx:in")
        self.assertIsNone(r.restore_error)
        self.assertFalse(r.active)

    def test_false_result_is_reported_and_others_still_tried(self):
        r = make_reamp({"gx:in": ["a:out", "b:out"]})
        with mock.patch.object(reamp.jackutil, "connect", side_effect=[False, True]) as c:
            r._finish()
        self.assertEqual(c.call_count, 2)
        self.assertIn("a:out -> gx:in", r.restore_error)
        self.assertNotIn("b:out", r.restore_error)
        self.assertFalse(r.active)
        self.assertEqual(r.changes, 1)

    def test_exception_is_caught_and_reported(self):
        r = make_reamp({"gx:in": ["a:out", "b:out"]})
        with mock.patch.object(reamp.jackutil, "connect",
                               side_effect=[OSError("no jack_connect"), True]) as c:
            r._finish()                        # must not raise
        self.assertEqual(c.call_count, 2)
        self.assertIn("a:out -> gx:in", r.restore_error)
        self.assertFalse(r.active)
        self.assertEqual(r.changes, 1)

    def test_error_clears_after_a_clean_session(self):
        r = make_reamp({"gx:in": ["a:out"]})
        with mock.patch.object(reamp.jackutil, "connect", return_value=False):
            r._finish()
        self.assertTrue(r.restore_error)
        r._session = {"take": "t.wav", "mode": "wet", "record": False, "output": None,
                      "saved": {"gx:in": ["a:out"]}, "amp_inputs": ["gx:in"]}
        with mock.patch.object(reamp.jackutil, "connect", return_value=True):
            r._finish()
        self.assertIsNone(r.restore_error)

    def test_error_details_retained_when_every_reconnect_fails(self):
        r = make_reamp({"gx:in": ["a:out"], "gx:in2": ["b:out"]})
        with mock.patch.object(reamp.jackutil, "connect", return_value=False) as c:
            r._finish()
        self.assertEqual(c.call_count, 2)
        self.assertIn("a:out -> gx:in", r.restore_error)
        self.assertIn("b:out -> gx:in2", r.restore_error)
        self.assertFalse(r.active)

    def test_second_finish_is_a_noop(self):
        r = make_reamp({"gx:in": ["a:out"]})
        with mock.patch.object(reamp.jackutil, "connect", return_value=False) as c:
            r._finish()
        self.assertEqual(c.call_count, 1)
        first = r.restore_error
        with mock.patch.object(reamp.jackutil, "connect", return_value=True) as c2:
            r._finish()
        self.assertEqual(c2.call_count, 0)
        self.assertEqual(r.restore_error, first)


if __name__ == "__main__":
    unittest.main()
