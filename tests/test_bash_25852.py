"""Regression tests for issue #25852: fake engine stall/crash injection.

The fake Guitarix engine gains opt-in fault injection driven by the
``GX_FAULTS`` environment variable:

* ``stall_set`` / ``stall_setpreset`` make the matching notification branch
  sleep 0.2s before applying its values.
* ``crash_on`` names a method whose branch calls ``os._exit(1)`` right after
  applying its values.

The tests drive ``FakeEngine._process_request`` directly, so they assert the
engine's observable behaviour (elapsed time, resulting values, process exit
code) rather than only inspecting the ``faults`` dict.
"""

import importlib.util
import json
import os
import subprocess
import sys
import time
import unittest


_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE_PATH = os.path.join(_HERE, 'fakes', 'engine.py')

_STALL_SECONDS = 0.2


def _load_engine_module():
    spec = importlib.util.spec_from_file_location('bash25852_fake_engine',
                                                  _ENGINE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine_mod = _load_engine_module()


class _FakeSocket:
    """Minimal stand-in for the client socket passed to _process_request."""

    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(data)

    def close(self):
        pass


def _set_request(*pairs):
    return json.dumps({
        'jsonrpc': '2.0',
        'method': 'set',
        'params': list(pairs),
    }).encode('utf-8')


def _setpreset_request(bank, preset):
    return json.dumps({
        'jsonrpc': '2.0',
        'method': 'setpreset',
        'params': [bank, preset],
    }).encode('utf-8')


class FakeEngineFaultInjectionTests(unittest.TestCase):

    def setUp(self):
        self._saved_faults = os.environ.get('GX_FAULTS')
        os.environ.pop('GX_FAULTS', None)

    def tearDown(self):
        if self._saved_faults is None:
            os.environ.pop('GX_FAULTS', None)
        else:
            os.environ['GX_FAULTS'] = self._saved_faults

    def make_engine(self, faults=None):
        if faults is None:
            os.environ.pop('GX_FAULTS', None)
        else:
            os.environ['GX_FAULTS'] = json.dumps(faults)
        return engine_mod.FakeEngine()

    # --- configuration ---------------------------------------------------

    def test_faults_default_to_empty(self):
        engine = self.make_engine(None)
        self.assertEqual(engine.faults, {})

    def test_faults_are_read_from_gx_faults_env(self):
        engine = self.make_engine({'stall_set': True, 'crash_on': 'setpreset'})
        self.assertEqual(engine.faults,
                         {'stall_set': True, 'crash_on': 'setpreset'})

    def test_malformed_gx_faults_falls_back_to_empty(self):
        os.environ['GX_FAULTS'] = 'this is not json'
        engine = engine_mod.FakeEngine()
        self.assertEqual(engine.faults, {})

    # --- stall on set ----------------------------------------------------

    def test_stall_set_sleeps_then_applies_values(self):
        engine = self.make_engine({'stall_set': True})
        sock = _FakeSocket()
        line = _set_request('amp.fuzz', 0.42, 'cab.on_off', 1)

        start = time.monotonic()
        engine._process_request(sock, line)
        elapsed = time.monotonic() - start

        self.assertGreaterEqual(elapsed, _STALL_SECONDS)
        self.assertAlmostEqual(engine.values['amp.fuzz'], 0.42)
        self.assertEqual(engine.values['cab.on_off'], 1)

    def test_set_without_stall_is_not_delayed(self):
        engine = self.make_engine({})
        sock = _FakeSocket()
        line = _set_request('amp.fuzz', 0.31)

        start = time.monotonic()
        engine._process_request(sock, line)
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, _STALL_SECONDS)
        self.assertAlmostEqual(engine.values['amp.fuzz'], 0.31)

    # --- stall on setpreset ----------------------------------------------

    def test_stall_setpreset_sleeps_then_applies_preset(self):
        engine = self.make_engine({'stall_setpreset': True})
        sock = _FakeSocket()
        line = _setpreset_request('Warm', 'Crunch')

        start = time.monotonic()
        engine._process_request(sock, line)
        elapsed = time.monotonic() - start

        self.assertGreaterEqual(elapsed, _STALL_SECONDS)
        self.assertAlmostEqual(engine.values['amp.fuzz'], 0.5)
        self.assertEqual(engine.values['system.current_bank'], 'Warm')
        self.assertEqual(engine.values['system.current_preset'], 'Crunch')

    # --- crash injection --------------------------------------------------

    def run_child(self, faults, method, params):
        code = (
            "import importlib.util, json, os, sys\n"
            "spec = importlib.util.spec_from_file_location('fake_engine',"
            " sys.argv[1])\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "os.environ['GX_FAULTS'] = sys.argv[2]\n"
            "engine = mod.FakeEngine()\n"
            "request = json.dumps({'jsonrpc': '2.0', 'method': sys.argv[3],"
            " 'params': json.loads(sys.argv[4])}).encode('utf-8')\n"
            "engine._process_request(None, request)\n"
            "sys.stdout.write('REACHED-END\\n')\n"
        )
        return subprocess.run(
            [sys.executable, '-c', code, _ENGINE_PATH,
             json.dumps(faults), method, json.dumps(params)],
            capture_output=True, text=True, timeout=30,
        )

    def test_crash_on_set_exits_nonzero(self):
        proc = self.run_child({'crash_on': 'set'}, 'set', ['amp.fuzz', 0.5])
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn('Traceback', proc.stderr)
        self.assertNotIn('REACHED-END', proc.stdout)

    def test_crash_on_setpreset_exits_nonzero(self):
        proc = self.run_child({'crash_on': 'setpreset'}, 'setpreset',
                              ['Warm', 'Crunch'])
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn('Traceback', proc.stderr)
        self.assertNotIn('REACHED-END', proc.stdout)

    def test_crash_on_set_does_not_affect_other_methods(self):
        proc = self.run_child({'crash_on': 'set'}, 'setpreset',
                              ['Warm', 'Crunch'])
        self.assertEqual(proc.returncode, 0)
        self.assertIn('REACHED-END', proc.stdout)


if __name__ == '__main__':
    unittest.main()
