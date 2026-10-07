"""Issue #23101: broadcast clients report their playback buffer to the server."""

import math
import os
import re
import unittest

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
    """A socketio stand-in that keeps handlers the way python-socketio does."""

    def __init__(self):
        self.handlers = {}              # namespace -> event -> handler

    def on(self, event, namespace="/"):
        def register(fn):
            self.handlers.setdefault(namespace, {})[event] = fn
            return fn
        return register

    def registered(self, namespace="/"):
        """The event handlers registered in a namespace, keyed by event."""
        return self.handlers.setdefault(namespace, {})


class PlaybackBufferTests(unittest.TestCase):
    def setUp(self):
        import monitor
        self.monitor = monitor
        self.server = FakeServer()
        monitor.register_broadcast_handlers(self.server)
        self.handlers = self.server.registered("/")
        # python-socketio calls an event handler as handler(sid, data)
        self.handler = self.handlers["playback_buffer"]
        self.sock = FakeSocket("sess-1")

    def test_valid_report_is_stored(self):
        self.handler(self.sock, {"seconds": 3.5})
        self.assertEqual(self.monitor.playback_buffer(self.sock), 3.5)

    def test_valid_report_updates_previous_value(self):
        self.handler(self.sock, {"seconds": 1.0})
        self.handler(self.sock, {"seconds": 7.25})
        self.assertEqual(self.monitor.playback_buffer(self.sock), 7.25)

    def test_negative_report_is_ignored(self):
        self.handler(self.sock, {"seconds": 4.0})
        self.handler(self.sock, {"seconds": -2.0})
        self.assertEqual(self.monitor.playback_buffer(self.sock), 4.0)

    def test_nan_report_is_ignored(self):
        self.handler(self.sock, {"seconds": 4.0})
        self.handler(self.sock, {"seconds": float("nan")})
        stored = self.monitor.playback_buffer(self.sock)
        self.assertEqual(stored, 4.0)
        self.assertFalse(math.isnan(stored))

    def test_infinite_report_is_ignored(self):
        self.handler(self.sock, {"seconds": 4.0})
        self.handler(self.sock, {"seconds": float("inf")})
        self.assertEqual(self.monitor.playback_buffer(self.sock), 4.0)

    def test_non_numeric_report_is_ignored(self):
        self.handler(self.sock, {"seconds": 4.0})
        self.handler(self.sock, {"seconds": "lots"})
        self.handler(self.sock, {"seconds": None})
        self.handler(self.sock, {})
        self.assertEqual(self.monitor.playback_buffer(self.sock), 4.0)

    def test_reports_are_per_session(self):
        other = FakeSocket("sess-2")
        self.handler(self.sock, {"seconds": 2.0})
        self.handler(other, {"seconds": 9.0})
        self.assertEqual(self.monitor.playback_buffer(self.sock), 2.0)
        self.assertEqual(self.monitor.playback_buffer(other), 9.0)

    def test_unknown_session_has_no_value(self):
        self.assertIsNone(self.monitor.playback_buffer(FakeSocket("never-seen")))

    def test_disconnect_forgets_the_value(self):
        self.handler(self.sock, {"seconds": 5.0})
        self.handlers["disconnect"](self.sock)
        self.assertIsNone(self.monitor.playback_buffer(self.sock))

    def test_disconnect_with_a_reason_forgets_the_value(self):
        self.handler(self.sock, {"seconds": 5.0})
        self.handlers["disconnect"](self.sock, "client namespace disconnect")
        self.assertIsNone(self.monitor.playback_buffer(self.sock))

    def test_only_playback_buffer_and_disconnect_are_registered(self):
        self.assertEqual(set(self.server.handlers["/"]),
                         {"playback_buffer", "disconnect"})

    def test_handler_is_called_the_way_python_socketio_calls_it(self):
        # handler(sid, data), not handler(data, sid): the other order is not a
        # report and must leave nothing behind
        self.handler({"seconds": 2.0}, self.sock)
        self.assertIsNone(self.monitor.playback_buffer(self.sock))
        self.handler(self.sock, {"seconds": 2.0})
        self.assertEqual(self.monitor.playback_buffer(self.sock), 2.0)

    def test_an_existing_disconnect_handler_is_still_called(self):
        server = FakeServer()
        seen = []
        server.on("disconnect")(lambda sid: seen.append(sid))   # registered before us
        self.monitor.register_broadcast_handlers(server)
        server.registered("/")["disconnect"]("sess-9")
        self.assertEqual(seen, ["sess-9"])

    def test_an_existing_disconnect_handler_that_takes_a_reason_gets_one(self):
        server = FakeServer()
        seen = []
        server.on("disconnect")(lambda sid, reason: seen.append((sid, reason)))
        self.monitor.register_broadcast_handlers(server)
        server.registered("/")["disconnect"]("sess-9", "client namespace disconnect")
        self.assertEqual(seen, [("sess-9", "client namespace disconnect")])


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

    def test_sends_the_buffered_seconds_as_the_payload(self):
        self.assertRegex(
            self.js,
            r"socket\.emit\(\s*['\"]playback_buffer['\"]\s*,\s*\{\s*seconds:")

    def test_stops_reporting_while_the_socket_is_down(self):
        self.assertIn("socket.connected", self.js)

    def test_clears_the_report_timer(self):
        self.assertRegex(self.js, r"clearInterval\(\s*bufferTimer\s*\)")


if __name__ == "__main__":
    unittest.main()
