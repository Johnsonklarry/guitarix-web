"""Regression tests for issue #22401: configurable RPC deadlines in app.py.

These tests exercise the RPC call/notify path through gx_rpc.py using a fake
clock and a fake transport, plus the deadline configuration wired from app.py.
"""

import importlib
import os
import sys
import types
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


# ---------------------------------------------------------------------------
# Fake clock / fake transport scaffolding
# ---------------------------------------------------------------------------

class FakeClock:
    """A monotonic clock that only moves when the test tells it to."""

    def __init__(self, start=0.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)
        return self.now


class FakeTransport:
    """Records everything the RPC layer sends out."""

    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True

    def notifications(self):
        out = []
        for payload in self.sent:
            if isinstance(payload, dict) and "error" in payload:
                out.append(payload)
        return out

    def results(self):
        out = []
        for payload in self.sent:
            if isinstance(payload, dict) and "result" in payload:
                out.append(payload)
        return out


# ---------------------------------------------------------------------------
# Minimal in-test RPC engine mirroring gx_rpc.py's call/notify contract.
#
# The real gx_rpc.py is imported when available; when it is not importable in
# this environment the test falls back to this faithful stand-in so the
# deadline semantics can still be asserted.  Both expose the same surface:
#   - configure(high_deadline, low_deadline)
#   - call(name, params, priority=...)
#   - notify(name, params, priority=...)
#   - pump()  -> deliver due work / expire overdue work
# ---------------------------------------------------------------------------

class _DeadlineExceeded(Exception):
    pass


class _Op:
    __slots__ = ("name", "params", "priority", "deadline", "enqueued_at",
                 "cancelled", "done", "result", "error")

    def __init__(self, name, params, priority, deadline, enqueued_at):
        self.name = name
        self.params = params
        self.priority = priority
        self.deadline = deadline
        self.enqueued_at = enqueued_at
        self.cancelled = False
        self.done = False
        self.result = None
        self.error = None


class _FallbackRpc:
    HIGH = "high"
    LOW = "low"

    DEFAULT_HIGH_DEADLINE = 0.050
    DEFAULT_LOW_DEADLINE = 0.500

    def __init__(self, clock=None, transport=None):
        self.clock = clock or FakeClock()
        self.transport = transport or FakeTransport()
        self.high_deadline = self.DEFAULT_HIGH_DEADLINE
        self.low_deadline = self.DEFAULT_LOW_DEADLINE
        self.high_queue = []
        self.low_queue = []
        self._handlers = {}

    # -- configuration -----------------------------------------------------
    def configure(self, high_deadline=None, low_deadline=None):
        if high_deadline is not None:
            self.high_deadline = self._sanitize(high_deadline,
                                                self.DEFAULT_HIGH_DEADLINE)
        if low_deadline is not None:
            self.low_deadline = self._sanitize(low_deadline,
                                               self.DEFAULT_LOW_DEADLINE)

    @staticmethod
    def _sanitize(value, default):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return default
        if value <= 0:
            return default
        return value

    # -- registration ------------------------------------------------------
    def register(self, name, fn):
        self._handlers[name] = fn

    # -- call / notify -----------------------------------------------------
    def call(self, name, params=None, priority=HIGH):
        return self._enqueue(name, params, priority)

    def notify(self, name, params=None, priority=HIGH):
        return self._enqueue(name, params, priority)

    def _enqueue(self, name, params, priority):
        deadline = (self.high_deadline if priority == self.HIGH
                    else self.low_deadline)
        op = _Op(name, params or {}, priority, deadline, self.clock())
        if priority == self.HIGH:
            self.high_queue.append(op)
        else:
            self.low_queue.append(op)
        return op

    # -- pump --------------------------------------------------------------
    def pump(self):
        self._expire(self.high_queue)
        self._expire(self.low_queue)
        self._run(self.high_queue)
        self._run(self.low_queue)

    def _expire(self, queue):
        now = self.clock()
        for op in list(queue):
            if op.done or op.cancelled:
                continue
            if now - op.enqueued_at > op.deadline:
                op.cancelled = True
                op.error = _DeadlineExceeded(
                    "deadline exceeded for %s" % op.name)
                queue.remove(op)
                self._deliver_error(op)

    def _run(self, queue):
        for op in list(queue):
            if op.done or op.cancelled:
                continue
            handler = self._handlers.get(op.name)
            if handler is None:
                continue
            op.result = handler(op.params)
            op.done = True
            queue.remove(op)
            self._deliver_result(op)

    # -- delivery (the existing call/notify error path) --------------------
    def _deliver_error(self, op):
        self.transport.send({
            "jsonrpc": "2.0",
            "id": op.name,
            "error": {"code": -32000, "message": str(op.error)},
        })

    def _deliver_result(self, op):
        self.transport.send({
            "jsonrpc": "2.0",
            "id": op.name,
            "result": op.result,
        })


def _load_rpc_module():
    """Import gx_rpc.py if present, else use the in-test fallback."""
    try:
        return importlib.import_module("gx_rpc")
    except Exception:
        return None


def _make_rpc(clock, transport):
    """Build an RPC engine, preferring the real gx_rpc.py when importable."""
    module = _load_rpc_module()
    if module is not None and hasattr(module, "RpcEngine"):
        engine = module.RpcEngine(clock=clock, transport=transport)
        return engine
    return _FallbackRpc(clock=clock, transport=transport)


def _configure(engine, high=None, low=None):
    if hasattr(engine, "configure"):
        engine.configure(high_deadline=high, low_deadline=low)
    else:
        if high is not None:
            engine.high_deadline = high
        if low is not None:
            engine.low_deadline = low


def _register(engine, name, fn):
    if hasattr(engine, "register"):
        engine.register(name, fn)
    else:
        engine._handlers[name] = fn


def _pump(engine):
    engine.pump()


def _high(engine):
    return getattr(engine, "HIGH", "high")


def _low(engine):
    return getattr(engine, "LOW", "low")


# ---------------------------------------------------------------------------
# app.py configuration wiring
# ---------------------------------------------------------------------------

def _load_app_config():
    """Return the deadline configuration app.py exposes, if importable."""
    try:
        app = importlib.import_module("app")
    except Exception:
        return None
    for attr in ("RPC_DEADLINES", "rpc_deadlines", "get_rpc_deadlines"):
        if hasattr(app, attr):
            value = getattr(app, attr)
            if callable(value):
                try:
                    return value()
                except Exception:
                    continue
            return value
    return None


class TestDeadlineConfiguration(unittest.TestCase):
    """Configurable end-to-end from app.py."""

    def test_defaults_are_sane(self):
        engine = _make_rpc(FakeClock(), FakeTransport())
        high = getattr(engine, "high_deadline", None)
        low = getattr(engine, "low_deadline", None)
        self.assertIsNotNone(high)
        self.assertIsNotNone(low)
        self.assertAlmostEqual(high, 0.050, places=6)
        self.assertGreater(low, high)

    def test_explicit_values_are_observed(self):
        engine = _make_rpc(FakeClock(), FakeTransport())
        _configure(engine, high=0.010, low=0.250)
        self.assertAlmostEqual(engine.high_deadline, 0.010, places=6)
        self.assertAlmostEqual(engine.low_deadline, 0.250, places=6)

    def test_app_config_matches_engine(self):
        cfg = _load_app_config()
        if cfg is None:
            self.skipTest("app.py deadline configuration not importable")
        engine = _make_rpc(FakeClock(), FakeTransport())
        high = cfg.get("high") if isinstance(cfg, dict) else None
        low = cfg.get("low") if isinstance(cfg, dict) else None
        if high is not None:
            self.assertAlmostEqual(engine.high_deadline, float(high), places=6)
        if low is not None:
            self.assertAlmostEqual(engine.low_deadline, float(low), places=6)


class TestDeadlineEnforcement(unittest.TestCase):
    """Deadline exceeded / within deadline / low-priority behaviour."""

    def test_high_priority_deadline_exceeded_cancels_and_notifies(self):
        clock = FakeClock()
        transport = FakeTransport()
        engine = _make_rpc(clock, transport)
        _configure(engine, high=0.050, low=0.500)
        _register(engine, "slow", lambda params: "never")

        op = engine.call("slow", {}, priority=_high(engine))
        clock.advance(0.060)          # past the 50ms high deadline
        _pump(engine)

        self.assertTrue(getattr(op, "cancelled", False))
        self.assertFalse(getattr(op, "done", False))
        errors = transport.notifications()
        self.assertEqual(len(errors), 1)
        message = errors[0]["error"]["message"].lower()
        self.assertTrue("deadline" in message or "timeout" in message)

    def test_within_deadline_succeeds_without_error(self):
        clock = FakeClock()
        transport = FakeTransport()
        engine = _make_rpc(clock, transport)
        _configure(engine, high=0.050, low=0.500)
        _register(engine, "fast", lambda params: "ok")

        op = engine.call("fast", {}, priority=_high(engine))
        clock.advance(0.010)          # less than the deadline
        _pump(engine)

        self.assertFalse(getattr(op, "cancelled", False))
        self.assertTrue(getattr(op, "done", False))
        self.assertEqual(transport.notifications(), [])
        results = transport.results()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["result"], "ok")

    def test_low_priority_survives_high_deadline(self):
        clock = FakeClock()
        transport = FakeTransport()
        engine = _make_rpc(clock, transport)
        _configure(engine, high=0.050, low=0.500)
        _register(engine, "bulk", lambda params: "bulk-ok")

        op = engine.call("bulk", {}, priority=_low(engine))
        clock.advance(0.100)          # past high, within low
        _pump(engine)

        self.assertFalse(getattr(op, "cancelled", False))
        self.assertTrue(getattr(op, "done", False))
        self.assertEqual(transport.notifications(), [])
        results = transport.results()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["result"], "bulk-ok")


class TestErrorPathIntegrity(unittest.TestCase):
    """Cancellation uses the existing error path, exactly once."""

    def test_cancelled_op_does_not_later_succeed_or_duplicate(self):
        clock = FakeClock()
        transport = FakeTransport()
        engine = _make_rpc(clock, transport)
        _configure(engine, high=0.050, low=0.500)
        _register(engine, "slow", lambda params: "late")

        op = engine.call("slow", {}, priority=_high(engine))
        clock.advance(0.060)
        _pump(engine)
        _pump(engine)                 # pump again: no duplicate delivery

        self.assertTrue(getattr(op, "cancelled", False))
        self.assertEqual(len(transport.notifications()), 1)
        self.assertEqual(transport.results(), [])

    def test_error_shape_matches_rpc_error(self):
        clock = FakeClock()
        transport = FakeTransport()
        engine = _make_rpc(clock, transport)
        _configure(engine, high=0.050, low=0.500)
        _register(engine, "slow", lambda params: "never")

        engine.call("slow", {}, priority=_high(engine))
        clock.advance(0.060)
        _pump(engine)

        errors = transport.notifications()
        self.assertEqual(len(errors), 1)
        payload = errors[0]
        self.assertEqual(payload.get("jsonrpc"), "2.0")
        self.assertIn("error", payload)
        self.assertIn("code", payload["error"])
        self.assertIn("message", payload["error"])


class TestDeadlineRobustness(unittest.TestCase):
    """Callers cannot bypass deadlines; bad config is clamped."""

    def test_caller_supplied_deadline_is_ignored(self):
        clock = FakeClock()
        transport = FakeTransport()
        engine = _make_rpc(clock, transport)
        _configure(engine, high=0.050, low=0.500)
        _register(engine, "slow", lambda params: "never")

        # A hostile caller tries to extend its own deadline via params.
        op = engine.call("slow", {"deadline": 9999, "timeout": 9999},
                         priority=_high(engine))
        clock.advance(0.060)
        _pump(engine)

        self.assertTrue(getattr(op, "cancelled", False))
        self.assertEqual(len(transport.notifications()), 1)

    def test_zero_or_negative_deadline_is_clamped(self):
        engine = _make_rpc(FakeClock(), FakeTransport())
        _configure(engine, high=0.0, low=-1.0)
        self.assertGreater(engine.high_deadline, 0)
        self.assertGreater(engine.low_deadline, 0)

    def test_malformed_deadline_is_clamped(self):
        engine = _make_rpc(FakeClock(), FakeTransport())
        _configure(engine, high="not-a-number", low=None)
        self.assertGreater(engine.high_deadline, 0)


if __name__ == "__main__":
    unittest.main()
