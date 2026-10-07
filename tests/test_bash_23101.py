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
    """A socketio stand-in that only remembers what was registered."""

    def __init__(self):
        self.handlers = {}

    def on(self, event):
        def register(fn):
            self.handlers[event] = fn
            return fn
        return register


class PlaybackBufferTests(unittest.TestCase):
    def setUp(self):
        import monitor
        self.monitor = monitor
        self.server = FakeServer()
        monitor.register_broadcast_handlers(self.server)
        self.handler = self.server.handlers["playback_buffer"]
        self.sock = FakeSocket("sess-1")

    def test_valid_report_is_stored(self):
        self.handler({"seconds": 3.5}, self.sock)
        self.assertEqual(self.monitor.playback_buffer(self.sock), 3.5)

    def test_valid_report_updates_previous_value(self):
        self.handler({"seconds": 1.0}, self.sock)
        self.handler({"seconds": 7.25}, self.sock)
        self.assertEqual(self.monitor.playback_buffer(self.sock), 7.25)

    def test_negative_report_is_ignored(self):
        self.handler({"seconds": 4.0}, self.sock)
        self.handler({"seconds": -2.0}, self.sock)
        self.assertEqual(self.monitor.playback_buffer(self.sock), 4.0)

    def test_nan_report_is_ignored(self):
        self.handler({"seconds": 4.0}, self.sock)
        self.handler({"seconds": float("nan")}, self.sock)
        stored = self.monitor.playback_buffer(self.sock)
        self.assertEqual(stored, 4.0)
        self.assertFalse(math.isnan(stored))

    def test_infinite_report_is_ignored(self):
        self.handler({"seconds": 4.0}, self.sock)
        self.handler({"seconds": float("inf")}, self.sock)
        self.assertEqual(self.monitor.playback_buffer(self.sock), 4.0)

    def test_non_numeric_report_is_ignored(self):
        self.handler({"seconds": 4.0}, self.sock)
        self.handler({"seconds": "lots"}, self.sock)
        self.handler({"seconds": None}, self.sock)
        self.handler({}, self.sock)
        self.assertEqual(self.monitor.playback_buffer(self.sock), 4.0)

    def test_reports_are_per_session(self):
        other = FakeSocket("sess-2")
        self.handler({"seconds": 2.0}, self.sock)
        self.handler({"seconds": 9.0}, other)
        self.assertEqual(self.monitor.playback_buffer(self.sock), 2.0)
        self.assertEqual(self.monitor.playback_buffer(other), 9.0)

    def test_unknown_session_has_no_value(self):
        self.assertIsNone(self.monitor.playback_buffer(FakeSocket("never-seen")))

    def test_disconnect_forgets_the_value(self):
        self.handler({"seconds": 5.0}, self.sock)
        self.server.handlers["disconnect"](self.sock)
        self.assertIsNone(self.monitor.playback_buffer(self.sock))

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


if __name__ == "__main__":
    unittest.main()
