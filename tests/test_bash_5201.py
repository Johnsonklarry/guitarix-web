"""
Regression test for the routing correction in app._broadcast_guarded_emit.

In broadcast mode every event that is not in BROADCAST_EMITS is dropped. But
op_done is not a fan-out event: it is a private reply, addressed to the one
client that started an operation via the ``to=<sid>`` kwarg. The broadcast
filter has to let an addressed emit through untouched, while the untargeted
fan-out events keep being filtered and sanitised. Only the events named in
app.BROADCAST_PRIVATE_EMITS get that treatment; an addressed emit of anything
else is filtered and sanitised like any other fan-out.

app.py imports flask and flask_socketio at module scope and this harness has
neither installed, so both are stood up in sys.modules before ``import app``.
The test only ever drives app._broadcast_guarded_emit; nothing here serves a
request.
"""

import logging
import sys
import types
import unittest
from unittest import mock


def _stub_modules():
    """The smallest flask/flask_socketio stand-ins that let app.py import."""
    flask = types.ModuleType("flask")

    class Flask:
        def __init__(self, *args, **kwargs):
            self.name = args[0] if args else "stub"
            self.config = {}
            self.static_folder = "static"
            self.template_folder = "templates"
            self.secret_key = None
            self.logger = logging.getLogger("flask.stub")

        def __getattr__(self, name):
            # route(), errorhandler(), template_global(), before_request() ...
            # are all decorator factories; handing the function back unchanged
            # is enough, because no request is ever dispatched here.
            def factory(*_args, **_kwargs):
                def decorator(fn):
                    return fn
                return decorator

            factory.__name__ = str(name)
            return factory

    flask.Flask = Flask
    flask.Response = type("Response", (), {})
    flask.abort = lambda *a, **kw: None
    flask.jsonify = lambda *a, **kw: {"args": a, "kwargs": kw}
    flask.render_template = lambda *a, **kw: ""
    flask.request = types.SimpleNamespace(sid=None)
    flask.send_from_directory = lambda *a, **kw: ""
    flask.session = {}
    flask.url_for = lambda *a, **kw: "/" + "/".join(str(p) for p in a)

    flask_socketio = types.ModuleType("flask_socketio")

    class SocketIO:
        """on()/emit() are plain attributes: app.py rebinds both at import."""

        def __init__(self, *args, **kwargs):
            self.on = lambda *a, **kw: (lambda fn: fn)
            self.emit = lambda *a, **kw: None
            self.start_background_task = lambda *a, **kw: None
            self.run = lambda *a, **kw: None

    flask_socketio.SocketIO = SocketIO
    flask_socketio.emit = lambda *a, **kw: None
    flask_socketio.join_room = lambda *a, **kw: None

    return {"flask": flask, "flask_socketio": flask_socketio}


class BroadcastRoutingTest(unittest.TestCase):
    """A targeted emit must survive broadcast filtering; fan-out must not."""

    @classmethod
    def setUpClass(cls):
        cls.stubs = _stub_modules()
        cls._stub_patcher = mock.patch.dict(sys.modules, cls.stubs, clear=False)
        cls._stub_patcher.start()
        cls.addClassCleanup(cls._stub_patcher.stop)

        global app
        import app  # noqa: F401  (binds the module global the tests use)

    def setUp(self):
        self.emitted = []

        def fake_emit(event, data=None, *a, **kw):
            self.emitted.append((event, data, a, kw))

        self._orig_emit = app._socketio_emit
        app._socketio_emit = fake_emit
        self.addCleanup(self._restore_emit)

    def _restore_emit(self):
        app._socketio_emit = self._orig_emit

    # -- import safety ----------------------------------------------------

    def test_app_imported_without_flask(self):
        """Collection must not die with ModuleNotFoundError: No module 'flask'."""
        self.assertIs(sys.modules.get("flask"), self.stubs["flask"])
        self.assertIs(sys.modules.get("flask_socketio"),
                      self.stubs["flask_socketio"])
        self.assertTrue(callable(app._broadcast_guarded_emit))

    # -- the correction ---------------------------------------------------

    def test_op_done_reaches_its_requester_in_broadcast_mode(self):
        with mock.patch.object(app, "BROADCAST", True):
            app._broadcast_guarded_emit("op_done", {"ok": True}, to="sid-abc")

        self.assertEqual(len(self.emitted), 1)
        event, data, args, kw = self.emitted[0]
        self.assertEqual(event, "op_done")
        self.assertEqual(data, {"ok": True})
        self.assertEqual(args, ())
        self.assertEqual(kw, {"to": "sid-abc"})

    def test_untargeted_op_done_is_still_dropped_in_broadcast_mode(self):
        with mock.patch.object(app, "BROADCAST", True):
            app._broadcast_guarded_emit("op_done", {"ok": True})

        self.assertEqual(self.emitted, [])

    def test_broadcast_snapshot_fanout_is_still_sanitised(self):
        with mock.patch.object(app, "BROADCAST", True), \
                mock.patch.object(app, "broadcast_snapshot",
                                  return_value={"sentinel": 1}):
            app._broadcast_guarded_emit("snapshot", {"stale": 2}, to=None)

        self.assertEqual(len(self.emitted), 1)
        event, data, _args, kw = self.emitted[0]
        self.assertEqual(event, "snapshot")
        self.assertEqual(data, {"sentinel": 1})
        # to=None means "no addressee", so this is still the filtered fan-out.
        self.assertFalse(kw.get("to"))

    def test_addressed_snapshot_is_still_sanitised(self):
        """A snapshot carrying to=<sid> is not a private reply: it sanitises."""
        with mock.patch.object(app, "BROADCAST", True), \
                mock.patch.object(app, "broadcast_snapshot",
                                  return_value={"sentinel": 1}):
            app._broadcast_guarded_emit("snapshot", {"stale": 2}, to="sid-abc")

        self.assertEqual(len(self.emitted), 1)
        event, data, _args, kw = self.emitted[0]
        self.assertEqual(event, "snapshot")
        self.assertEqual(data, {"sentinel": 1})
        self.assertEqual(kw, {"to": "sid-abc"})

    def test_addressed_rec_is_still_trimmed(self):
        with mock.patch.object(app, "BROADCAST", True):
            app._broadcast_guarded_emit(
                "rec", {"recording": True, "count": 3, "secret": "x"},
                to="sid-abc")

        self.assertEqual(len(self.emitted), 1)
        event, data, _args, kw = self.emitted[0]
        self.assertEqual(event, "rec")
        self.assertEqual(data, {"recording": True, "count": 3})
        self.assertEqual(kw, {"to": "sid-abc"})

    def test_addressed_event_outside_the_private_allow_list_is_dropped(self):
        """Only a named private reply may skip the fan-out filter."""
        with mock.patch.object(app, "BROADCAST", True):
            app._broadcast_guarded_emit("toast", {"text": "hi"}, to="sid-abc")

        self.assertEqual(self.emitted, [])

    def test_broadcast_rec_is_trimmed(self):
        with mock.patch.object(app, "BROADCAST", True):
            app._broadcast_guarded_emit(
                "rec", {"recording": True, "count": 3, "secret": "x"})

        self.assertEqual(len(self.emitted), 1)
        event, data, _args, kw = self.emitted[0]
        self.assertEqual(event, "rec")
        self.assertEqual(data, {"recording": True, "count": 3})
        self.assertEqual(kw, {})

    # -- wiring -----------------------------------------------------------

    def test_allowed_emit_routes_through_the_broadcast_filter(self):
        """socketio.emit is the policy guard, and it must reach the sanitizer."""
        with mock.patch.object(app, "BROADCAST", True), \
                mock.patch.object(app, "broadcast_snapshot",
                                  return_value={"sentinel": 1}):
            app.socketio.emit("snapshot", {"stale": 2})

        self.assertEqual(len(self.emitted), 1)
        event, data, _args, _kw = self.emitted[0]
        self.assertEqual(event, "snapshot")
        self.assertEqual(data, {"sentinel": 1})

    def test_denied_emit_never_reaches_the_broadcast_filter(self):
        with mock.patch.object(app.policy, "mode", "broadcast"):
            app.socketio.emit("toast", {"text": "hi"})

        self.assertEqual(self.emitted, [])

    def test_non_broadcast_passthrough_unchanged(self):
        with mock.patch.object(app, "BROADCAST", False):
            app._broadcast_guarded_emit("op_done", {"ok": True}, to="sid-abc")

        self.assertEqual(len(self.emitted), 1)
        event, data, args, kw = self.emitted[0]
        self.assertEqual(event, "op_done")
        self.assertEqual(data, {"ok": True})
        self.assertEqual(args, ())
        self.assertEqual(kw, {"to": "sid-abc"})


if __name__ == "__main__":
    unittest.main()
