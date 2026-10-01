#!/usr/bin/env python3
"""
Night 45: the monitor queue holds a bounded number of SECONDS of audio, an old
encoder run's pump cannot reach a new run's listeners, and encoder stderr is
drained while stdout is still flowing. CPU-only: fake processes, no ffmpeg/JACK.

    python3 tests/night_45_tests.py
"""

import io
import os
import queue
import sys
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import monitor


def frame(n):
    # MPEG-1 layer III, 160 kb/s, 48 kHz, no padding: 480 bytes, 24 ms
    return bytes((0xff, 0xfb, 0xa4, n & 0xff)) + bytes([n & 0xff]) * 476


class Recording(io.BytesIO):
    """A pipe that only offers read1 and remembers how much each call asked for."""

    def __init__(self, data):
        super().__init__(data)
        self.sizes = []

    def read(self, size=-1):
        raise AssertionError("read() waits to fill the buffer; use read1()")

    def read1(self, size=-1):
        self.sizes.append(size)
        return super().read(size)


class Gated:
    """A pipe that hands out its steps in order; an Event step blocks until set."""

    def __init__(self, steps):
        self.steps = list(steps)

    def read1(self, size=-1):
        while self.steps:
            step = self.steps.pop(0)
            if isinstance(step, threading.Event):
                if not step.wait(5):
                    raise AssertionError("gate never opened")
                continue
            return step
        return b""


class FakeProc:
    def __init__(self, steps, stderr=()):
        self.stdout = Gated(steps)
        self.stderr = Gated(stderr)
        self.dead = False

    def poll(self):
        return 0 if self.dead else None

    def terminate(self):
        self.dead = True

    kill = terminate

    def wait(self, timeout=None):
        return 0


def wait_for(test, cond, what):
    end = time.time() + 3
    while time.time() < end:
        if cond():
            return
        time.sleep(0.01)
    test.fail("timed out waiting for " + what)


class LagBudget(unittest.TestCase):
    def test_slow_listener_is_held_to_the_time_budget_in_whole_frames(self):
        m = monitor.Monitor(lambda: [])
        slow, quick = queue.Queue(), queue.Queue()
        owned = {slow, quick}
        out = Recording(b"".join(frame(i) for i in range(200)))
        proc = mock.Mock(stdout=out, stderr=Recording(b""))
        m._pump(proc, owned)
        items = list(slow.queue)
        self.assertIsNone(items[-1])
        frames = items[:-1]
        self.assertLessEqual(sum(map(len, frames)) / monitor.BYTES_PER_SEC,
                             monitor.LAG_BUDGET)
        self.assertGreaterEqual(len(frames), 15)           # not shedding too eagerly
        self.assertTrue(all(len(f) == 480 and f[:3] == b"\xff\xfb\xa4" for f in frames))
        # the newest audio survives, contiguously
        self.assertEqual([f[3] for f in frames],
                         list(range(200 - len(frames), 200)))
        self.assertEqual(list(quick.queue), items)
        self.assertLessEqual(max(out.sizes), 1024)

    def test_reads_are_small_and_encoder_keeps_frames_independent(self):
        self.assertLessEqual(monitor.CHUNK, 1024)
        args = monitor.encoder_args()
        self.assertEqual(args[args.index("-reservoir") + 1], "0")

    def test_frame_sizes(self):
        self.assertEqual(monitor._frame_size(bytearray(b"\xff\xfb\xa4\x00")), 480)
        self.assertEqual(monitor._frame_size(bytearray(b"\xff\xfb\xa0\x00")), 522)
        self.assertEqual(monitor._frame_size(bytearray(b"\xff\xfb\xa2\x00")), 523)
        self.assertEqual(monitor._frame_size(bytearray(b"\x00\x01\x02\x03")), 0)

    def test_bytes_that_are_not_mp3_still_flow(self):
        m = monitor.Monitor(lambda: [])
        q = queue.Queue()
        data = bytes(range(10)) * 5
        m._pump(mock.Mock(stdout=Recording(data), stderr=Recording(b"")), {q})
        items = list(q.queue)
        self.assertIsNone(items.pop())
        self.assertEqual(b"".join(items), data)


class Generations(unittest.TestCase):
    def test_old_pump_cannot_feed_or_end_a_new_encoder_run(self):
        gate1, gate2 = threading.Event(), threading.Event()
        p1 = FakeProc([gate1, frame(1)])
        p2 = FakeProc([frame(2), gate2])
        procs = [p1, p2]
        popen = mock.Mock(side_effect=lambda *a, **k: procs.pop(0))
        got = {"A": [], "B": []}

        def run(name, m):
            for chunk in m.listen():
                got[name].append(chunk)

        with mock.patch.object(monitor.subprocess, "Popen", popen), \
                mock.patch.object(monitor.Monitor, "_wire", lambda self, proc: None):
            m = monitor.Monitor(lambda: [])
            ta = threading.Thread(target=run, args=("A", m), daemon=True)
            ta.start()
            wait_for(self, lambda: popen.call_count == 1, "first encoder")
            p1.dead = True                     # it died, but its pump is still late
            tb = threading.Thread(target=run, args=("B", m), daemon=True)
            tb.start()
            wait_for(self, lambda: popen.call_count == 2, "second encoder")
            wait_for(self, lambda: got["B"] == [frame(2)], "audio for B")
            gate1.set()                        # now the old pump finishes
            ta.join(3)
            self.assertFalse(ta.is_alive())
            self.assertEqual(got["A"], [frame(1)])
            time.sleep(0.2)
            self.assertTrue(tb.is_alive(), "old pump ended the new run's listener")
            self.assertEqual(got["B"], [frame(2)])
            gate2.set()
            tb.join(3)
            self.assertFalse(tb.is_alive())
            self.assertEqual(got["B"], [frame(2)])


class Stderr(unittest.TestCase):
    def test_sustained_stderr_is_drained_while_stdout_is_active(self):
        drained = threading.Event()

        class Errors:
            def __init__(self):
                self.left = 256 * 1024

            def read1(self, size=-1):
                if not self.left:
                    return b""
                n = min(size, self.left)
                self.left -= n
                if not self.left:
                    drained.set()
                return b"x" * n

        class Audio:
            done = False

            def read1(self, size=-1):
                if self.done:
                    return b""
                if not drained.wait(3):
                    raise AssertionError("stdout waited for stderr to drain")
                self.done = True
                return frame(7)

        m = monitor.Monitor(lambda: [])
        q = queue.Queue()
        proc = mock.Mock(stdout=Audio(), stderr=Errors())
        worker = threading.Thread(target=m._pump, args=(proc, {q}), daemon=True)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "pump stalled behind stderr")
        self.assertEqual(q.get_nowait(), frame(7))
        self.assertIsNone(q.get_nowait())


if __name__ == "__main__":
    unittest.main()
