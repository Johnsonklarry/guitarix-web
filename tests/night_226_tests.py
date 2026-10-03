"""Regression tests for delta-based reconnect synchronization (issue #128 part 2).

Standalone: python3 tests/night_226_tests.py
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeSocketIO:
    def __init__(self):
        self.emitted = []

    def on(self, event, *a, **kw):
        def register(fn):
            return fn
        return register

    def emit(self, event, data=None, *a, **kw):
        self.emitted.append((event, data))

    def start_background_task(self, target, *a, **kw):
        return None

    def sleep(self, *_a, **_kw):
        return None

    def run(self, *a, **kw):
        return None


def _install_stubs():
    flask = types.ModuleType("flask")

    class _Flask:
        def __init__(self, *a, **kw):
            self.config = {}
            self.static_folder = "/tmp"

        def route(self, *a, **kw):
            def deco(fn):
                return fn
            return deco

        def template_global(self, *a, **kw):
            def deco(fn):
                return fn
            return deco

        def before_request(self, fn):
            return fn

    flask.Flask = _Flask
    flask.Response = object
    flask.abort = lambda *a, **kw: None
    flask.jsonify = lambda *a, **kw: None
    flask.render_template = lambda *a, **kw: ""
    flask.request = types.SimpleNamespace(args={}, method="GET", endpoint=None,
                                          files={}, sid=None)
    flask.send_from_directory = lambda *a, **kw: None
    flask.url_for = lambda *a, **kw: ""
    sys.modules["flask"] = flask

    fsi = types.ModuleType("flask_socketio")
    fsi.SocketIO = lambda *a, **kw: FakeSocketIO()
    sys.modules["flask_socketio"] = fsi

    for name in ("controls", "gx_rpc", "presets_io", "recorder", "reamp",
                 "backing", "monitor", "jackutil"):
        mod = types.ModuleType(name)
        sys.modules.setdefault(name, mod)

    gx = sys.modules["gx_rpc"]
    gx.GuitarixRPC = lambda *a, **kw: types.SimpleNamespace()
    gx.RpcError = type("RpcError", (Exception,), {})
    gx.RpcMethodMissing = type("RpcMethodMissing", (Exception,), {})
    gx.PRESET_METHODS = {}

    rec = sys.modules["recorder"]
    rec.Recorder = lambda *a, **kw: types.SimpleNamespace()
    rec.SOURCE_CLIENT = "client"

    rp = sys.modules["reamp"]
    rp.Reamp = lambda *a, **kw: types.SimpleNamespace()
    rp.ReampError = type("ReampError", (Exception,), {})

    bk = sys.modules["backing"]
    bk.Backing = lambda *a, **kw: types.SimpleNamespace()
    bk.BackingError = type("BackingError", (Exception,), {})

    sys.modules["monitor"].Monitor = lambda *a, **kw: types.SimpleNamespace()


_install_stubs()
import app  # noqa: E402


class ReconnectSyncTests(unittest.TestCase):
    def setUp(self):
        app.socketio.emitted = []

    def test_valid_version_emits_deltas(self):
        app.state.version = 5
        app.state.deltas = [(4, {"a": 1}), (5, {"b": 2})]
        app.client_connected(3)
        events = [e for e, _ in app.socketio.emitted]
        self.assertIn("deltas", events)
        self.assertNotIn("snapshot", events)

    def test_stale_version_falls_back_to_snapshot(self):
        app.state.version = 5
        app.state.deltas = [(4, {"a": 1})]
        app.client_connected(1)
        events = [e for e, _ in app.socketio.emitted]
        self.assertIn("snapshot", events)
        self.assertNotIn("deltas", events)

    def test_missing_version_falls_back_to_snapshot(self):
        app.state.version = 5
        app.state.deltas = [(4, {"a": 1})]
        app.client_connected(None)
        events = [e for e, _ in app.socketio.emitted]
        self.assertIn("snapshot", events)
        self.assertNotIn("deltas", events)


if __name__ == "__main__":
    unittest.main()
