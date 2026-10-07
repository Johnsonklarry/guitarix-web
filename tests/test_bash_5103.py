import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fake_socketio_module():
    """A minimal flask_socketio stand-in that records registrations."""
    mod = types.ModuleType("flask_socketio")

    class FakeSocketIO:
        def __init__(self, app=None, **kw):
            self.app = app
            self.handlers = {}
            self.emitted = []

        def on(self, event, *a, **kw):
            def register(fn):
                self.handlers[event] = fn
                return fn
            return register

        def emit(self, event, *a, **kw):
            self.emitted.append((event, a, kw))

        def start_background_task(self, target, *a, **kw):
            return None

    mod.SocketIO = FakeSocketIO
    return mod


class AccessPolicyTest(unittest.TestCase):
    def setUp(self):
        self._saved = {}
        for name in ("flask_socketio", "flask", "app"):
            self._saved[name] = sys.modules.pop(name, None)
        sys.modules["flask_socketio"] = _fake_socketio_module()

    def tearDown(self):
        for name, mod in self._saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    def _load_app(self, env):
        with mock.patch.dict(os.environ, env, clear=False):
            import app as app_module
        return app_module

    def test_deny_by_default_blocks_unregistered_event(self):
        app_module = self._load_app({"GX_DEMO_ONLY": "1"})
        policy = app_module.policy
        self.assertEqual(policy.mode, "demo")
        self.assertFalse(policy.allows("set_param"))

        @policy.on("set_param")
        def _handler(payload):
            return "mutated"

        sio = app_module.socketio
        self.assertIn("set_param", sio.handlers)
        self.assertFalse(sio.handlers["set_param"]({"v": 1}))

    def test_allowed_event_passes_through(self):
        app_module = self._load_app({"GX_DEMO_ONLY": "1"})
        policy = app_module.policy
        self.assertTrue(policy.allows("snapshot"))

        @policy.on("snapshot")
        def _handler(payload):
            return "ok"

        sio = app_module.socketio
        self.assertEqual(sio.handlers["snapshot"]({}), "ok")

    def test_public_state_filters_fields_per_mode(self):
        app_module = self._load_app({"GX_DEMO_ONLY": "1"})
        state = {"connected": True, "preset": "p", "secret": "x"}
        out = app_module.policy.public_state(state)
        self.assertEqual(out, {"connected": True, "preset": "p"})

        studio = app_module.AccessPolicy(app_module.socketio, mode="studio")
        self.assertEqual(studio.public_state(state), state)

    def test_emit_denied_for_unallowed_event(self):
        app_module = self._load_app({"GX_DEMO_ONLY": "1"})
        sio = app_module.socketio
        sio.emitted.clear()
        sio.emit("set_param", {"v": 1})
        self.assertEqual(sio.emitted, [])

    def test_emit_allowed_for_snapshot(self):
        app_module = self._load_app({"GX_DEMO_ONLY": "1"})
        sio = app_module.socketio
        sio.emitted.clear()
        sio.emit("snapshot", {"connected": True})
        self.assertEqual(len(sio.emitted), 1)
        self.assertEqual(sio.emitted[0][0], "snapshot")


if __name__ == "__main__":
    unittest.main()
