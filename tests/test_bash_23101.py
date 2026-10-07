"""Issue #23101: broadcast clients report their playback buffer to the server."""

import inspect
import math
import os
import re
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class FakeSocket:
    """The least a socket needs to be, for the handler under test."""

    def __init__(self, sid="sess-1"):
        self.sid = sid
        self.emitted = []

    def emit(self, event, data=None):
        self.emitted.append((event, data))


class FakeServer:
    """A socketio stand-in that only remembers what was registered."""

    def __init__(self):
        self.handlers = {}

    def on(self, event):
        def register(fn):
            self.handlers[event] = fn
            return fn
        return register


class FakeRequest:
    """
    flask.request as a socket event handler sees it: inside a socket event the
    server puts the connection in the request context, and that is where a
    handler that wants the sid has to look.
    """

    def __init__(self, sid):
        self.sid = sid


class NoRequestContext:
    """flask.request outside a request context: touching .sid raises."""

    @property
    def sid(self):
        raise RuntimeError("Working outside of request context.")


class PlaybackBufferTests(unittest.TestCase):
    """
    The handlers are driven the way python-socketio drives them: an event
    handler receives the payload the client sent and nothing else, and the
    connection it arrived on is the one in the request context. A disconnect
    handler receives the reason the client went away.
    """

    def setUp(self):
        import monitor
        self.monitor = monitor
        self.server = FakeServer()
        monitor.register_broadcast_handlers(self.server)
        self.handler = self.server.handlers["playback_buffer"]
        self.disconnect = self.server.handlers["disconnect"]
        self.sock = FakeSocket("sess-1")
        patcher = mock.patch.object(self.monitor, "request", FakeRequest("sess-1"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_valid_report_is_stored(self):
        self.handler({"seconds": 3.5})
        self.assertEqual(self.monitor.playback_buffer("sess-1"), 3.5)

    def test_valid_report_updates_previous_value(self):
        self.handler({"seconds": 1.0})
        self.handler({"seconds": 7.25})
        self.assertEqual(self.monitor.playback_buffer("sess-1"), 7.25)

    def test_negative_report_is_ignored(self):
        self.handler({"seconds": 4.0})
        self.handler({"seconds": -2.0})
        self.assertEqual(self.monitor.playback_buffer("sess-1"), 4.0)

    def test_nan_report_is_ignored(self):
        self.handler({"seconds": 4.0})
        self.handler({"seconds": float("nan")})
        stored = self.monitor.playback_buffer("sess-1")
        self.assertEqual(stored, 4.0)
        self.assertFalse(math.isnan(stored))

    def test_infinite_report_is_ignored(self):
        self.handler({"seconds": 4.0})
        self.handler({"seconds": float("inf")})
        self.assertEqual(self.monitor.playback_buffer("sess-1"), 4.0)

    def test_non_numeric_report_is_ignored(self):
        self.handler({"seconds": 4.0})
        self.handler({"seconds": "lots"})
        self.handler({"seconds": None})
        self.handler({})
        self.assertEqual(self.monitor.playback_buffer("sess-1"), 4.0)

    def test_reports_are_per_session(self):
        self.handler({"seconds": 2.0})
        with mock.patch.object(self.monitor, "request", FakeRequest("sess-2")):
            self.handler({"seconds": 9.0})
        self.assertEqual(self.monitor.playback_buffer("sess-1"), 2.0)
        self.assertEqual(self.monitor.playback_buffer("sess-2"), 9.0)

    def test_unknown_session_has_no_value(self):
        self.assertIsNone(self.monitor.playback_buffer("never-seen"))

    def test_report_sent_with_a_socket_is_stored_against_it(self):
        self.handler({"seconds": 6.0}, FakeSocket("sess-3"))
        self.assertEqual(self.monitor.playback_buffer("sess-3"), 6.0)

    def test_report_outside_a_request_context_does_not_raise(self):
        with mock.patch.object(self.monitor, "request", NoRequestContext()):
            self.handler({"seconds": 1.0})
        self.assertIsNone(self.monitor.playback_buffer("sess-1"))

    def test_disconnect_forgets_the_value(self):
        self.handler({"seconds": 5.0})
        self.disconnect()
        self.assertIsNone(self.monitor.playback_buffer("sess-1"))

    def test_disconnect_accepts_the_reason_python_socketio_passes(self):
        self.handler({"seconds": 5.0})
        self.disconnect("client namespace disconnect")
        self.assertIsNone(self.monitor.playback_buffer("sess-1"))

    def test_disconnect_accepts_a_socket_passed_positionally(self):
        self.handler({"seconds": 5.0})
        self.disconnect(self.sock)
        self.assertIsNone(self.monitor.playback_buffer("sess-1"))

    def test_disconnect_without_a_request_context_does_not_raise(self):
        self.handler({"seconds": 5.0})
        with mock.patch.object(self.monitor, "request", NoRequestContext()):
            self.disconnect("client namespace disconnect")
        self.assertEqual(self.monitor.playback_buffer("sess-1"), 5.0)

    def test_handlers_take_the_python_socketio_call_signatures(self):
        inspect.signature(self.handler).bind({"seconds": 1.0})
        inspect.signature(self.disconnect).bind()
        inspect.signature(self.disconnect).bind("client namespace disconnect")

    def test_only_playback_buffer_and_disconnect_are_registered(self):
        self.assertEqual(set(self.server.handlers), {"playback_buffer", "disconnect"})


class BroadcastClientTests(unittest.TestCase):
    """The page must measure and emit its buffered-ahead seconds."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "static", "broadcast.js"), encoding="utf-8") as fh:
            cls.js = fh.read()

    def test_emits_playback_buffer(self):
        self.assertIn("playback_buffer", self.js)
        self.assertRegex(self.js, r"socket\.emit\(\s*['\"]playback_buffer['\"]")

    def test_measures_buffered_ranges_against_current_time(self):
        self.assertIn("buffered", self.js)
        self.assertIn("currentTime", self.js)

    def test_reports_periodically(self):
        self.assertRegex(self.js, r"setInterval\(")

    def test_never_reports_a_negative_number(self):
        self.assertRegex(self.js, r"Math\.max\(\s*0\s*,")

    def test_guards_against_non_finite_values(self):
        self.assertIn("isFinite", self.js)

    def test_header_documents_the_one_outbound_event(self):
        # The page does emit one event now, and the server allows it; the
        # header must not keep claiming the page is silent.
        self.assertNotIn("there is no emit in this file", self.js)
        self.assertIn("playback_buffer", self.js)


if __name__ == "__main__":
    unittest.main()
