import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
import backing


class Night236Tests(unittest.TestCase):
    def test_backing_async_duration_service(self):
        b = backing.Backing(directory="/tmp/fake_backing_night")
        fake_path = "/tmp/fake_backing_night/track.mp3"
        mtime = 12345.0

        callback_holder = {}
        mock_service = MagicMock()
        def fake_get_duration(path, cb):
            callback_holder["cb"] = cb
            return None

        mock_service.getDuration.side_effect = fake_get_duration
        sys.modules["ffprobeService"] = mock_service

        res = b._duration(fake_path, mtime)
        self.assertIsNone(res)
        self.assertTrue(mock_service.getDuration.called)
        self.assertIn("cb", callback_holder)

        callback_holder["cb"](123.45)
        self.assertEqual(b._duration(fake_path, mtime), 123.45)


if __name__ == "__main__":
    unittest.main()
