import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
import backing


class BackingDurationTest(unittest.TestCase):
    def test_duration_caching_and_async_probe(self):
        b = backing.Backing(directory="/tmp/fake_backing_test")
        fake_path = "/tmp/fake_backing_test/track.mp3"
        mtime = 12345.0

        mock_service = MagicMock()
        def fake_get_duration(path, cb):
            self._cb = cb
            return None

        mock_service.getDuration.side_effect = fake_get_duration
        sys.modules["ffprobeService"] = mock_service

        dur = b._duration(fake_path, mtime)
        self.assertIsNone(dur)
        self.assertEqual(b._durations[fake_path], (mtime, None))

        self._cb(42.5)
        self.assertEqual(b._durations[fake_path], (mtime, 42.5))
        self.assertEqual(b._duration(fake_path, mtime), 42.5)
        mock_service.getDuration.assert_called_once()


if __name__ == "__main__":
    unittest.main()
