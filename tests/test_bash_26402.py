import unittest
from monitor import Monitor

class TestMonitorStatus(unittest.TestCase):
    def test_status_includes_metrics(self):
        def dummy_sources(): return []
        m = Monitor(dummy_sources)
        status = m.status()
        self.assertIn("xruns", status)
        self.assertIn("latency", status)
        self.assertEqual(status["xruns"], 0)
        self.assertEqual(status["latency"]["roundtrip_ms"], 0)

    def test_xrun_accumulation(self):
        def dummy_sources(): return []
        m = Monitor(dummy_sources)
        m.xruns += 1
        self.assertEqual(m.status()["xruns"], 1)
