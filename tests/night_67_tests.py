#!/usr/bin/env python3
"""
Night 67: the ticker reads Recorder.recording and Player.playing without a
lock while stop() clears _proc. The properties used to read self._proc twice
(None check, then .poll()), so a stop between the two reads raised
AttributeError inside the ticker. They must read it exactly once.

    python3 tests/night_67_tests.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(HERE, "stubs"), ROOT]

from recorder import Recorder  # noqa: E402
from player import Player      # noqa: E402


class _FakeProc:
    def poll(self):
        return None            # still running


class _Vanishing:
    """
    Descriptor standing in for `_proc`: the first read returns a live process,
    every later read returns None, as if stop() ran between two reads.
    """

    def __init__(self):
        self.reads = 0

    def __get__(self, obj, cls=None):
        self.reads += 1
        return _FakeProc() if self.reads == 1 else None


class TickerFlagsRace(unittest.TestCase):
    def _check(self, base, attr):
        desc = _Vanishing()
        cls = type("Flaky", (base,), {"_proc": desc})
        obj = object.__new__(cls)                 # skip __init__: no I/O
        self.assertIs(getattr(obj, attr), True)   # AttributeError before the fix
        self.assertEqual(desc.reads, 1)

    def test_recorder_recording_reads_proc_once(self):
        self._check(Recorder, "recording")

    def test_player_playing_reads_proc_once(self):
        self._check(Player, "playing")


if __name__ == "__main__":
    unittest.main()
