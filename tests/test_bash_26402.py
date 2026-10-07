import io
import queue
import unittest

import monitor
from monitor import Monitor


def dummy_sources():
    return []


class _FakeEncoder:
    """Enough of a subprocess.Popen for _pump: a stdout to read, a poll()."""

    def __init__(self, data):
        self.stdout = io.BytesIO(data)
        self.stderr = io.BytesIO(b"")

    def poll(self):
        return None


class TestMonitorStatus(unittest.TestCase):
    def test_status_reports_listeners_and_running(self):
        m = Monitor(dummy_sources)
        status = m.status()
        self.assertEqual(status["listeners"], 0)
        self.assertFalse(status["running"])


class TestLagBudget(unittest.TestCase):
    """_pump sheds against max_lag_bytes. A Monitor built without it raises
    AttributeError on the first chunk and every listener dies with it."""

    def test_default_budget_is_kept(self):
        m = Monitor(dummy_sources)
        self.assertEqual(m.lag_budget, monitor.DEFAULT_LAG_BUDGET)
        self.assertEqual(m.max_lag_bytes,
                         int(monitor.DEFAULT_LAG_BUDGET * monitor.BYTES_PER_SEC))

    def test_custom_budget_is_kept(self):
        m = Monitor(dummy_sources, lag_budget=1.5)
        self.assertEqual(m.lag_budget, 1.5)
        self.assertEqual(m.max_lag_bytes, int(1.5 * monitor.BYTES_PER_SEC))

    def test_pump_reaches_listeners(self):
        m = Monitor(dummy_sources)
        q = queue.Queue()
        m._pump(_FakeEncoder(b"hello"), {q})     # would raise without max_lag_bytes
        self.assertEqual(q.get_nowait(), b"hello")
        self.assertIsNone(q.get_nowait())        # and the run's end is signalled
