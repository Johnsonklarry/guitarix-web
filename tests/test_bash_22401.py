"""Regression tests for issue #22401: configurable RPC deadlines in app.py.

These tests exercise the RPC call/notify path through gx_rpc.py using a fake
clock and a fake transport, plus the deadline configuration wired from app.py.
"""

import importlib
import os
import sys
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
# Production RPC engine access.
#
# The deadline behaviour in this suite must be verified against the real
# gx_rpc.RpcEngine; an in-test stand-in would let the suite pass without ever
# exercising the code under review.  The helpers below therefore fail loudly
# when the production engine is unavailable.  The engine is expected to
# expose:
#   - configure(high_deadline, low_deadline)
#   - register(name, fn)
#   - call(name, params, priority=...)
#   - notify(name, params, priority=...)
#   - pump()  -> deliver due work / expire overdue work
# ---------------------------------------------------------------------------

DOCUMENTED_HIGH_DEADLINE = 0.050
DOCUMENTED_LOW_DEADLINE = 0.500


def _load_rpc_module():
    """Import the production gx_rpc module, or return None when unavailable."""
    try:
        return importlib.import_module("gx_rpc")
    except Exception:
        return None


def _require_rpc_engine():
    """Return gx_rpc.RpcEngine, failing loudly when production code is absent."""
    module = _load_rpc_module()
    engine_cls = getattr(module, "RpcEngine", None)
    if engine_cls is None:
        raise AssertionError(
            "gx_rpc.RpcEngine is not importable; the RPC deadline tests must "
            "exercise the production engine, not an in-test stand-in")
    return engine_cls


def _make_rpc(clock, transport):
    """Build the production RPC engine; never fall back to a stand-in."""
    return _require_rpc_engine()(clock=clock, transport=transport)


def _configure(engine, high=None, low=None):
    engine.configure(high_deadline=high, low_deadline=low)


def _register(engine, name, fn):
    engine.register(name, fn)


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


class TestProductionCodeIsExercised(unittest.TestCase):
    """The suite must fail if the production engine is unavailable."""

    def test_real_rpc_engine_is_importable(self):
        module = _load_rpc_module()
        self.assertIsNotNone(
            module,
            "gx_rpc.py must be importable so deadline behaviour is exercised "
            "against production code")
        self.assertTrue(
            hasattr(module, "RpcEngine"),
            "gx_rpc.py must expose RpcEngine")

    def test_make_rpc_returns_real_engine(self):
        engine = _make_rpc(FakeClock(), FakeTransport())
        self.assertIsInstance(engine, _require_rpc_engine())


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
        self.assertIsNotNone(
            cfg,
            "app.py must expose its RPC deadline configuration via one of "
            "RPC_DEADLINES / rpc_deadlines / get_rpc_deadlines")
        self.assertIsInstance(
            cfg, dict,
            "app.py deadline configuration must expose 'high'/'low' values")
        self.assertIn("high", cfg)
        self.assertIn("low", cfg)
        engine = _make_rpc(FakeClock(), FakeTransport())
        self.assertAlmostEqual(engine.high_deadline, float(cfg["high"]),
                               places=6)
        self.assertAlmostEqual(engine.low_deadline, float(cfg["low"]),
                               places=6)


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
        self.assertAlmostEqual(engine.high_deadline,
                               DOCUMENTED_HIGH_DEADLINE, places=6)
        self.assertAlmostEqual(engine.low_deadline,
                               DOCUMENTED_LOW_DEADLINE, places=6)

    def test_malformed_deadline_is_clamped(self):
        engine = _make_rpc(FakeClock(), FakeTransport())
        _configure(engine, high="not-a-number", low=None)
        self.assertAlmostEqual(engine.high_deadline,
                               DOCUMENTED_HIGH_DEADLINE, places=6)
        self.assertGreater(engine.low_deadline, 0)


if __name__ == "__main__":
    unittest.main()
