#!/usr/bin/env python3
"""
Regression tests for issue #127 part 2: live control ops go out on the
high-priority lane, bulk ops (import/export/diagnostics) on the low one,
and the high lane is scheduled first when both have pending work.

Standalone: python3 tests/night_223_tests.py
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gx_rpc


class FakeTransport(gx_rpc.GuitarixRPC):
    """A GuitarixRPC whose socket is a list, so nothing real is touched."""

    def __init__(self):
        super().__init__()
        self.sent = []
        self.sent_lock = threading.Lock()
        self.connected = True
        self._sock = object()          # truthy, never used: _send is overridden

    def _send(self, method, params, call_id=None):
        with self.sent_lock:
            self.sent.append((method, list(params), call_id))

    def _connect(self):                # never called; the fake is already up
        raise AssertionError("the fake transport must not connect")

    def _read_loop(self):
        return


class LaneOrderingTests(unittest.TestCase):
    def setUp(self):
        self.rpc = FakeTransport()
        self.rpc._lane_thread = threading.Thread(target=self.rpc._lane_loop,
                                                 name="fake-lanes", daemon=True)
        self.rpc._lane_thread.start()

    def tearDown(self):
        self.rpc.stop()

    def _methods(self):
        with self.rpc.sent_lock:
            return [m for m, _, _ in self.rpc.sent]

    def test_high_lane_is_scheduled_ahead_of_low(self):
        # hold the lane thread off the socket so both lanes fill up first
        gate = threading.Event()
        real_send = self.rpc._send

        def gated(method, params, call_id=None):
            gate.wait(2.0)
            real_send(method, params, call_id)

        self.rpc._send = gated
        self.rpc.notify("bulk_import", ["a"], lane=gx_rpc.LOW)
        self.rpc.notify("bulk_export", ["b"], lane=gx_rpc.LOW)
        self.rpc.notify("set", ["amp.gain", 5], lane=gx_rpc.HIGH)
        time.sleep(0.1)
        gate.set()
        deadline = time.time() + 2.0
        while len(self._methods()) < 3 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(self._methods()[0], "set",
                         "the high-priority lane must be dispatched first")

    def test_default_lane_is_high(self):
        self.rpc.notify("set", ["amp.gain", 1])
        deadline = time.time() + 2.0
        while not self._methods() and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(self._methods(), ["set"])

    def test_low_lane_still_gets_sent(self):
        self.rpc.notify("bulk", [], lane=gx_rpc.LOW)
        deadline = time.time() + 2.0
        while not self._methods() and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(self._methods(), ["bulk"])

    def test_unknown_lane_is_rejected(self):
        with self.assertRaises(ValueError):
            self.rpc.notify("set", [], lane=99)


class AppRoutingTests(unittest.TestCase):
    """The app's own call sites must name the right lane."""

    def _source(self, name):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, name)) as f:
            return f.read()

    def test_set_param_uses_high_lane(self):
        src = self._source("app.py")
        self.assertIn("rpc.set({pid: value}, lane=gx_rpc.HIGH)", src)

    def test_preset_change_uses_high_lane(self):
        src = self._source("app.py")
        self.assertIn("rpc.set_preset(bank, preset, lane=gx_rpc.HIGH)", src)

    def test_import_save_all_uses_low_lane(self):
        src = self._source("app.py")
        self.assertIn('rpc.preset_save_as(bank, preset["name"], lane=gx_rpc.LOW)', src)

    def test_export_uses_low_lane(self):
        src = self._source("app.py")
        self.assertIn("_snapshot_state(lane=gx_rpc.LOW)", src)

    def test_diagnostics_use_low_lane(self):
        src = self._source("diagnose.py")
        self.assertIn("rpc.parameter_list(lane=gx_rpc.LOW)", src)
        self.assertIn("rpc.banks(lane=gx_rpc.LOW)", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
