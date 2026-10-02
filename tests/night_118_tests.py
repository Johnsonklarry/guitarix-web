#!/usr/bin/env python3
"""
Night 118: fault injection at every reamp and export transition.

    python3 tests/night_118_tests.py

The fakes can now inject delayed ports, dropped connections, stalled
subprocesses and crashes, log every injection, and the reamp still restores
the guitar's wiring and reports what didn't come back. No JACK is needed:
the fake tools are driven directly and jackutil.connect is patched.
"""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import reamp  # noqa: E402


def load_fakejack():
    spec = importlib.util.spec_from_file_location(
        "night_118_fakejack", os.path.join(HERE, "fakes", "fakejack.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeJackFaultTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FAKE_JACK_DIR": self.dir})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.fj = load_fakejack()

    def test_dropped_connection_is_refused_and_logged(self):
        self.fj.set_faults(drop_connect=[["system:capture_1", "gx_head_amp:in_0"]])
        self.assertFalse(self.fj.connect("system:capture_1", "gx_head_amp:in_0"))
        kinds = [f["kind"] for f in self.fj.read_faults()]
        self.assertIn("drop_connect", kinds)

    def test_dropped_disconnect_is_refused_and_logged(self):
        self.fj.set_faults(drop_disconnect=[["system:capture_1", "gx_head_amp:in_0"]])
        self.assertFalse(self.fj.disconnect("system:capture_1", "gx_head_amp:in_0"))
        self.assertIn("drop_disconnect", [f["kind"] for f in self.fj.read_faults()])

    def test_delayed_port_is_hidden_then_appears(self):
        self.fj.set_faults(drop_ports=["gx_head_fx:in_0"])
        self.assertNotIn("gx_head_fx:in_0", self.fj.ports())
        self.fj.set_faults(drop_ports=None)
        self.assertIn("gx_head_fx:in_0", self.fj.ports())

    def test_crash_makes_the_tool_fail(self):
        self.fj.set_faults(crash=["jack_lsp"])
        self.assertEqual(self.fj.ports(), {})
        self.assertIn("crash", [f["kind"] for f in self.fj.read_faults()])

    def test_stall_is_logged(self):
        self.fj.set_faults(stall=0.01)
        self.fj.connections_of("system:capture_1")
        self.assertIn("stall", [f["kind"] for f in self.fj.read_faults()])

    def test_clear_faults_forgets_the_log(self):
        self.fj.set_faults(crash=["jack_lsp"])
        self.fj.ports()
        self.fj.clear_faults()
        self.assertEqual(self.fj.read_faults(), [])


class FakeEngineFaultTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "night_118_engine", os.path.join(HERE, "fakes", "engine.py"))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def make(self, **env):
        with mock.patch.dict(os.environ, env):
            return self.module.FakeEngine()

    def test_crash_is_logged_and_reported(self):
        log = os.path.join(tempfile.mkdtemp(), "faults.log")
        engine = self.make(GX_FAULT_CRASH="setpreset", GX_FAULT_LOG=log)
        self.assertTrue(engine._inject("setpreset"))
        with open(log) as f:
            self.assertEqual(json.loads(f.readline())["kind"], "crash")

    def test_drop_is_logged(self):
        log = os.path.join(tempfile.mkdtemp(), "faults.log")
        engine = self.make(GX_FAULT_DROP="get", GX_FAULT_LOG=log)
        self.assertTrue(engine._inject("get"))
        with open(log) as f:
            self.assertEqual(json.loads(f.readline())["kind"], "drop")

    def test_stall_is_logged(self):
        log = os.path.join(tempfile.mkdtemp(), "faults.log")
        engine = self.make(GX_FAULT_STALL="0.01", GX_FAULT_LOG=log)
        self.assertFalse(engine._inject("get"))
        with open(log) as f:
            self.assertEqual(json.loads(f.readline())["kind"], "stall")

    def test_clean_engine_injects_nothing(self):
        log = os.path.join(tempfile.mkdtemp(), "faults.log")
        engine = self.make(GX_FAULT_LOG=log)
        self.assertFalse(engine._inject("get"))
        self.assertFalse(os.path.exists(log))


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


class ReampFaultTests(unittest.TestCase):
    def test_dropped_reconnect_is_reported_and_others_still_tried(self):
        r = make_reamp({"gx:in": ["a:out", "b:out"]})
        with mock.patch.object(reamp.jackutil, "connect", side_effect=[False, True]) as c:
            r._finish()
        self.assertEqual(c.call_count, 2)
        self.assertIn("a:out -> gx:in", r.restore_error)
        self.assertFalse(r.active)

    def test_crashed_reconnect_is_caught_and_reported(self):
        r = make_reamp({"gx:in": ["a:out", "b:out"]})
        with mock.patch.object(reamp.jackutil, "connect",
                               side_effect=[OSError("jack_connect crashed"), True]) as c:
            r._finish()                        # must not raise
        self.assertEqual(c.call_count, 2)
        self.assertIn("a:out -> gx:in", r.restore_error)
        self.assertFalse(r.active)


if __name__ == "__main__":
    unittest.main()
