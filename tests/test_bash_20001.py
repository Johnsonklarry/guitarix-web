"""Regression test for issue #20001: timeout + error callback for ping2.

socket.emit('ping2', ...) used to wait for its ack for ever. These tests pin
down the timeout and the error callback that replace that hang.

Stdlib only: flask is never imported for real, so the module under test has to
import cleanly without it.
"""

import importlib
import sys
import time
import types
import unittest
from unittest.mock import MagicMock, patch

# The module under test imports jackutil, which shells out to JACK tools. This
# test never drives JACK, so stand in for it when it isn't installed.
try:
    import jackutil  # noqa: F401
except ImportError:
    jackutil = types.ModuleType("jackutil")
    jackutil.available = lambda: False
    jackutil.ports = lambda *a, **k: []
    jackutil.connect = lambda *a, **k: False
    jackutil.pairs = lambda a, b: []
    sys.modules["jackutil"] = jackutil

import spike_latency


def wait_for(predicate, timeout=2.0):
    """Wait for a background timer, without a long fixed sleep."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _FlaskBlocker(object):
    """A meta path finder that makes 'import flask' fail on demand."""

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("flask", "flask_socketio"):
            raise ImportError("blocked by the test: %s" % name)
        return None


class Ping2EmitTests(unittest.TestCase):

    def setUp(self):
        self.sio = MagicMock()

    def test_emit_ping2_starts_the_timeout(self):
        """Emitting ping2 arms a timer and passes an ack callback."""
        with patch.object(spike_latency.threading, "Timer") as timer_cls:
            spike_latency.emit_ping2(self.sio, {"t": 12345})

        self.assertEqual(self.sio.emit.call_count, 1)
        args, kwargs = self.sio.emit.call_args
        self.assertEqual(args[0], "ping2")
        self.assertIsInstance(args[1], dict)
        self.assertEqual(args[1]["t"], 12345)
        self.assertTrue(callable(kwargs["callback"]))

        self.assertEqual(timer_cls.call_count, 1)
        self.assertEqual(timer_cls.call_args[0][0], spike_latency.PING2_TIMEOUT)
        self.assertTrue(callable(timer_cls.call_args[0][1]))
        self.assertTrue(timer_cls.return_value.start.called)

    def test_ack_cancels_the_timeout(self):
        """An ack before the deadline cancels the timer and fires nothing."""
        err = MagicMock()
        with patch.object(spike_latency.threading, "Timer") as timer_cls:
            spike_latency.emit_ping2(self.sio, {"t": 12345}, on_timeout=err)
            ack = self.sio.emit.call_args.kwargs["callback"]
            ack({"t": 12346})
            self.assertTrue(timer_cls.return_value.cancel.called)
            self.assertEqual(err.call_count, 0)

    def test_ack_before_a_real_timeout_fires_nothing(self):
        """Same, against a real timer with a short budget."""
        err = MagicMock()
        spike_latency.emit_ping2(self.sio, {"t": 1}, timeout=0.05, on_timeout=err)
        ack = self.sio.emit.call_args.kwargs["callback"]
        ack({"t": 2})
        time.sleep(0.2)
        self.assertEqual(err.call_count, 0)

    def test_missing_ack_fires_the_error_callback(self):
        """No ack within the timeout invokes the error callback exactly once."""
        err = MagicMock()
        spike_latency.emit_ping2(self.sio, {"t": 1}, timeout=0.05, on_timeout=err)
        self.assertTrue(wait_for(lambda: err.call_count == 1))
        self.assertIsInstance(err.call_args[0][0], dict)
        self.assertEqual(err.call_count, 1)

    def test_late_ack_neither_fires_again_nor_raises(self):
        """An ack after the timeout is ignored, and must not blow up."""
        err = MagicMock()
        spike_latency.emit_ping2(self.sio, {"t": 1}, timeout=0.05, on_timeout=err)
        ack = self.sio.emit.call_args.kwargs["callback"]
        self.assertTrue(wait_for(lambda: err.call_count == 1))
        ack({"t": 2})
        time.sleep(0.05)
        self.assertEqual(err.call_count, 1)

    def test_module_imports_without_flask(self):
        """A stdlib-only interpreter can still import the module."""
        blocker = _FlaskBlocker()
        saved = {}
        for name in ("flask", "flask_socketio", "spike_latency"):
            if name in sys.modules:
                saved[name] = sys.modules.pop(name)
        sys.meta_path.insert(0, blocker)
        try:
            module = importlib.import_module("spike_latency")
            self.assertFalse(module.HAVE_FLASK)
            self.assertIsInstance(module.PING2_TIMEOUT, float)
            self.assertTrue(callable(module.emit_ping2))
            self.assertTrue(callable(module.on_ping2))
        finally:
            sys.meta_path.remove(blocker)
            sys.modules.pop("spike_latency", None)
            sys.modules.update(saved)

    def test_registered_handler_emits_ping2(self):
        """The 'ping2' entry point is the same emit, with a callback."""
        with patch.object(spike_latency, "socketio", self.sio), \
                patch.object(spike_latency.threading, "Timer"):
            reply = spike_latency.on_ping2({"t": 999})

        args, kwargs = self.sio.emit.call_args
        self.assertEqual(args[0], "ping2")
        self.assertIsInstance(args[1], dict)
        self.assertTrue(callable(kwargs["callback"]))
        self.assertEqual(reply["client"], 999)
        self.assertIn("server", reply)


if __name__ == "__main__":
    unittest.main()
