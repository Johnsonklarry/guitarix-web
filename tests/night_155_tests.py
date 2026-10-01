import unittest
from unittest.mock import MagicMock, patch

import reamp


class TestReampDisconnectFailure(unittest.TestCase):
    def test_reamp_start_fails_when_disconnect_fails(self):
        rec = MagicMock()
        rec._resolve.return_value = "/tmp/dry.wav"
        rec.recording = False
        r = reamp.Reamp(rec)

        with patch("jackutil.available", return_value=True), \
             patch("jackutil.ports", return_value=["gx_head_amp:in_0"]), \
             patch("jackutil.connections", return_value=["system:capture_1"]), \
             patch("jackutil.disconnect", return_value=False) as mock_disc, \
             patch("jackutil.connect", return_value=True) as mock_conn:
            with self.assertRaises(reamp.ReampError):
                r.start("take1", "take1 (dry).wav")

            self.assertFalse(r.active)
            mock_disc.assert_called_once_with("system:capture_1", "gx_head_amp:in_0")
            mock_conn.assert_called_once_with("system:capture_1", "gx_head_amp:in_0")


if __name__ == "__main__":
    unittest.main()
