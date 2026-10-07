"""Regression tests for issue #12601: the in-memory operation journal in `done`.

`done()` may be handed a durable operation id. The first call records the
result; a repeat call with the same id replays the recorded result rather than
reporting the freshly supplied one. The journal is bounded by a maximum number
of entries and by a TTL, after which the id is forgotten and processed again.

`app` needs flask / flask-socketio to run. Where those packages are missing --
this suite is stdlib-only -- permissive stand-ins are registered in sys.modules
first, so the module can still be imported and `done` driven in isolation. No
sockets are opened, no files are touched and no credentials are used.
"""

import importlib
import importlib.util
import os
import sys
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class _StandIn:
    """A placeholder that tolerates any attribute access, call or subscript."""

    def __init__(self, label="stand-in"):
        object.__setattr__(self, "_label", label)

    def __call__(self, *args, **kwargs):
        return _StandIn(self._label + "()")

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _StandIn("%s.%s" % (self._label, name))

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)

    def __getitem__(self, key):
        return _StandIn("%s[%r]" % (self._label, key))

    def __setitem__(self, key, value):
        pass

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __bool__(self):
        return True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _module_attr(module_name):
    def attr(name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _StandIn("%s.%s" % (module_name, name))
    return attr


def _install_stubs():
    """Register stand-ins for the third-party packages that are not installed."""
    stubbed = []
    for name in ("flask", "flask_socketio", "simple_websocket"):
        try:
            available = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            available = False
        if available:
            continue
        module = types.ModuleType(name)
        module.__dict__["__getattr__"] = _module_attr(name)
        sys.modules[name] = module
        stubbed.append(name)
    return stubbed


def _load_app():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    _install_stubs()
    return importlib.import_module("app")


app = _load_app()


class _EmitRecorder:
    """Stands in for the socketio object and records what `done` pushes."""

    def __init__(self):
        self.calls = []

    def emit(self, event, data=None, *args, **kwargs):
        self.calls.append((event, data, kwargs))

    def payloads(self, event="op_done"):
        return [data for name, data, _ in self.calls if name == event]


class DoneJournalTest(unittest.TestCase):
    def setUp(self):
        app._done_journal.clear()
        self._max = app.DONE_JOURNAL_MAX
        self._ttl = app.DONE_JOURNAL_TTL
        self._socketio = app.socketio
        self.recorder = _EmitRecorder()
        app.socketio = self.recorder

    def tearDown(self):
        app.socketio = self._socketio
        app.DONE_JOURNAL_MAX = self._max
        app.DONE_JOURNAL_TTL = self._ttl
        app._done_journal.clear()

    def test_new_operation_is_recorded_and_emitted(self):
        app.done("invalid", False, op_id="op-1")
        self.assertEqual(self.recorder.payloads(),
                         [{"op": "invalid", "ok": False}])
        self.assertIn("op-1", app._done_journal)

    def test_duplicate_operation_replays_the_cached_result(self):
        app.done("delete", True, op_id="op-2")
        recorded = app._done_journal["op-2"]
        # A second request with the same id must not re-run the operation: the
        # original result comes back even though different arguments arrive.
        app.done("something-else", False, op_id="op-2")
        self.assertEqual(self.recorder.payloads(),
                         [{"op": "delete", "ok": True},
                          {"op": "delete", "ok": True}])
        self.assertEqual(len(app._done_journal), 1)
        self.assertEqual(app._done_journal["op-2"], recorded)

    def test_operation_without_id_is_not_journaled(self):
        app.done("invalid", False)
        app.done("invalid", False)
        self.assertEqual(self.recorder.payloads(),
                         [{"op": "invalid", "ok": False},
                          {"op": "invalid", "ok": False}])
        self.assertEqual(len(app._done_journal), 0)

    def test_journal_is_bounded_and_evicts_the_oldest_entry(self):
        now = time.time()
        for index in range(10000):
            app._done_journal["old-%05d" % index] = ("op", False, now)
        self.assertEqual(len(app._done_journal), 10000)

        app.done("new", True, op_id="new")

        self.assertEqual(len(app._done_journal), app.DONE_JOURNAL_MAX)
        self.assertIn("new", app._done_journal)
        self.assertNotIn("old-00000", app._done_journal)
        self.assertIn("old-09999", app._done_journal)

    def test_expired_operation_is_treated_as_new(self):
        stale = time.time() - (app.DONE_JOURNAL_TTL + 60)
        app._done_journal["op-3"] = ("stale", True, stale)

        app.done("fresh", False, op_id="op-3")

        self.assertEqual(self.recorder.payloads(),
                         [{"op": "fresh", "ok": False}])
        self.assertIn("op-3", app._done_journal)
        self.assertGreater(app._done_journal["op-3"][2], stale)

    def test_journal_ttl_is_one_day(self):
        self.assertEqual(app.DONE_JOURNAL_TTL, 24 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
