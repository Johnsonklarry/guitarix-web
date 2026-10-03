import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))
sys.path.insert(0, str(repo_root))

if "jackutil" not in sys.modules:
    try:
        import jackutil
    except ImportError:
        sys.modules["jackutil"] = MagicMock()

mock_ffprobe_service = ModuleType("ffprobeService")
sys.modules["ffprobeService"] = mock_ffprobe_service

import recorder
recorder.ffprobeService = mock_ffprobe_service


class RecorderDurationRegressionTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.rec = recorder.Recorder(directory=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_cached_duration_returns_immediately(self):
        path = os.path.join(self.test_dir, "take1.wav")
        mtime = 12345.0
        self.rec._durations[path] = (mtime, 15.5)

        mock_get_duration = MagicMock()
        mock_ffprobe_service.getDuration = mock_get_duration

        t0 = time.time()
        duration = self.rec._duration(path, mtime)
        elapsed = time.time() - t0

        self.assertEqual(duration, 15.5)
        self.assertLess(elapsed, 0.05)
        mock_get_duration.assert_not_called()

    def test_uncached_duration_placeholder_and_async_update(self):
        path = os.path.join(self.test_dir, "take2.wav")
        mtime = 67890.0

        def fake_get_duration(p):
            time.sleep(0.1)
            return 42.0

        mock_ffprobe_service.getDuration = MagicMock(side_effect=fake_get_duration)

        t0 = time.time()
        duration = self.rec._duration(path, mtime)
        elapsed = time.time() - t0

        self.assertIsNone(duration)
        self.assertLess(elapsed, 0.05)

        deadline = time.time() + 2.0
        while time.time() < deadline:
            cached = self.rec._durations.get(path)
            if cached and cached[0] == mtime and cached[1] == 42.0:
                break
            time.sleep(0.02)

        self.assertEqual(self.rec._durations.get(path), (mtime, 42.0))
        self.assertEqual(self.rec._duration(path, mtime), 42.0)


if __name__ == "__main__":
    unittest.main()
