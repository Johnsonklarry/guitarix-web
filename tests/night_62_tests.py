#!/usr/bin/env python3
"""
Night 62: an ffmpeg startup failure must not escape pump_jack.

    python3 tests/night_62_tests.py
"""

import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import spike_latency


class FfmpegStartupFailure(unittest.TestCase):
    def setUp(self):
        self._saved = dict(spike_latency.state)
        spike_latency.state["proc"] = None
        spike_latency.state["wired"] = None

    def tearDown(self):
        spike_latency.state.clear()
        spike_latency.state.update(self._saved)

    def test_missing_ffmpeg_is_reported_not_raised(self):
        with mock.patch.object(spike_latency.subprocess, "Popen",
                               side_effect=FileNotFoundError("ffmpeg not found")):
            spike_latency.pump_jack("gx_head_amp")   # must not raise
        self.assertIsNone(spike_latency.state["proc"])
        self.assertIn("ffmpeg could not start", spike_latency.state["wired"])
        self.assertIn("ffmpeg not found", spike_latency.state["wired"])

    def test_permission_error_is_reported_too(self):
        with mock.patch.object(spike_latency.subprocess, "Popen",
                               side_effect=PermissionError("denied")):
            spike_latency.pump_jack("gx_head_amp")
        self.assertIn("denied", spike_latency.state["wired"])


if __name__ == "__main__":
    unittest.main()
