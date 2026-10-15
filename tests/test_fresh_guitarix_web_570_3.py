"""Issue #570: backing uploads are judged by their leading bytes, not just by
their extension, and a rejected upload never lands on disk.

No network and no guitarix: the Flask test client drives the real /backing
route, and app.backing.dir is pointed at a temporary directory so the test
can see exactly what was and wasn't stored.
"""

import io
import os
import shutil
import sys
import tempfile
import unittest

# Tests live in tests/; the app modules live at the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Importing app must not switch on auth, demo mode or broadcast mode.
os.environ.pop("GX_PASSWORD", None)
os.environ["GX_DEMO_ONLY"] = ""
os.environ["GX_BROADCAST"] = ""

import app  # noqa: E402


# A text file with a .wav name: the extension says audio, the bytes don't.
NOT_AUDIO = b"definitely not audio, just words\n" * 4

# The smallest thing that still starts with a genuine RIFF/WAVE header.
MINIMAL_WAV = (
    b"RIFF" + (36).to_bytes(4, "little") + b"WAVE"
    + b"fmt " + (16).to_bytes(4, "little")
    + (1).to_bytes(2, "little")      # PCM
    + (1).to_bytes(2, "little")      # mono
    + (8000).to_bytes(4, "little")   # sample rate
    + (8000).to_bytes(4, "little")   # byte rate
    + (1).to_bytes(2, "little")      # block align
    + (8).to_bytes(2, "little")      # bits per sample
    + b"data" + (0).to_bytes(4, "little")
)


class BackingUploadMagicTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gx-backing-test-")
        self._orig_dir = app.backing.dir
        self._orig_on_change = app.backing.on_change
        app.backing.dir = self.tmp
        app.backing.on_change = lambda: None       # no socket fan-out in tests
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def tearDown(self):
        app.backing.dir = self._orig_dir
        app.backing.on_change = self._orig_on_change
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _upload(self, blob, filename):
        return self.client.post(
            "/backing",
            data={"file": (io.BytesIO(blob), filename)},
            content_type="multipart/form-data",
        )

    def test_renamed_text_is_rejected_and_nothing_is_written(self):
        resp = self._upload(NOT_AUDIO, "song.wav")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_genuine_wav_header_is_stored(self):
        resp = self._upload(MINIMAL_WAV, "song.wav")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])
        self.assertEqual(os.listdir(self.tmp), ["song.wav"])
        with open(os.path.join(self.tmp, "song.wav"), "rb") as f:
            self.assertEqual(f.read(4), b"RIFF")


if __name__ == "__main__":
    unittest.main()
