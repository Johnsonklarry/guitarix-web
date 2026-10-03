"""Regression tests for issue #46 part 2: PCM transport and jitter buffer client.

Run: python3 tests/night_238_tests.py
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import monitor  # noqa: E402


class PcmEndpointTests(unittest.TestCase):
    def test_framing_constants(self):
        self.assertEqual(monitor.PCM_CHUNK, monitor.PCM_FRAMES * monitor.PCM_CHANNELS * 2)
        self.assertEqual(monitor.PCM_FRAMES, 256)
        self.assertEqual(monitor.PCM_CHANNELS, 2)
        self.assertEqual(monitor.PCM_RATE, 48000)

    def test_response_headers(self):
        m = monitor.Monitor(lambda: [])
        resp = m.pcm_response()
        self.assertEqual(resp.headers["Content-Type"], monitor.PCM_CONTENT_TYPE)
        self.assertEqual(resp.headers["X-Audio-Rate"], "48000")
        self.assertEqual(resp.headers["X-Audio-Channels"], "2")
        self.assertEqual(resp.headers["X-Audio-Frames"], "256")
        self.assertEqual(resp.headers["X-Audio-Format"], "s16le")
        self.assertEqual(resp.headers["Cache-Control"], "no-store")

    def test_pcm_args_are_raw_s16le(self):
        args = monitor.pcm_args()
        self.assertIn("s16le", args)
        self.assertIn("-ar", args)
        self.assertIn("48000", args)
        self.assertNotIn("libmp3lame", args)

    def test_chunks_are_exactly_one_frame(self):
        # A fake ffmpeg that emits a short read: the generator must pad it to
        # PCM_CHUNK, never send a short chunk, so the client can frame blindly.
        class FakeProc(object):
            def __init__(self):
                self.stdout = self
                self._reads = [b"\x01" * 100, b"\x02" * monitor.PCM_CHUNK, b""]

            def read1(self, n):
                return self._reads.pop(0) if self._reads else b""

            def read(self, n):
                return self.read1(n)

            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

        m = monitor.Monitor(lambda: [])
        orig = monitor.subprocess.Popen
        monitor.subprocess.Popen = lambda *a, **k: FakeProc()
        try:
            chunks = []
            for c in m.pcm():
                chunks.append(c)
                if len(chunks) == 2:
                    break
        finally:
            monitor.subprocess.Popen = orig
        self.assertEqual(len(chunks), 2)
        for c in chunks:
            self.assertEqual(len(c), monitor.PCM_CHUNK)
        self.assertEqual(chunks[0][:100], b"\x01" * 100)
        self.assertEqual(chunks[0][100:], b"\x00" * (monitor.PCM_CHUNK - 100))


class WorkletClientTests(unittest.TestCase):
    def test_broadcast_js_loads_the_worklet(self):
        path = os.path.join(ROOT, "static", "broadcast.js")
        with open(path) as f:
            src = f.read()
        self.assertIn("audioWorklet.addModule", src)
        self.assertIn("pcm-worklet.js", src)
        self.assertIn("AudioWorkletNode", src)
        self.assertIn("/audio.pcm", src)
        # the fallback must still exist for insecure contexts
        self.assertIn("ScriptProcessor", src)


if __name__ == "__main__":
    unittest.main()
