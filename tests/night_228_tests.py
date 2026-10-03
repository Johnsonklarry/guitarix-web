import unittest
from unittest.mock import patch, MagicMock
import jackutil

class JackConnectionRetryTests(unittest.TestCase):
    @patch("subprocess.run")
    @patch("time.sleep")
    def test_jack_connection_retry_success_after_failures(self, mock_sleep, mock_run):
        mock_fail = MagicMock(returncode=1)
        mock_success = MagicMock(returncode=0)
        mock_run.side_effect = [mock_fail, mock_fail, mock_success]

        result = jackutil.connect("src:out", "dst:in", max_retries=3, backoff_factor=0.01)
        self.assertTrue(result)
        self.assertEqual(mock_run.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("subprocess.run")
    @patch("time.sleep")
    def test_jack_connection_retry_exceeds_max_retries(self, mock_sleep, mock_run):
        mock_fail = MagicMock(returncode=1)
        mock_run.side_effect = [mock_fail, mock_fail, mock_fail, mock_fail]

        result = jackutil.connect("src:out", "dst:in", max_retries=3, backoff_factor=0.01)
        self.assertFalse(result)
        self.assertEqual(mock_run.call_count, 4)
        self.assertEqual(mock_sleep.call_count, 3)

    @patch("jackutil.connections")
    @patch("jackutil.disconnect")
    def test_cleanup_connections(self, mock_disconnect, mock_connections):
        mock_connections.return_value = ["dst:in_1", "dst:in_2"]
        jackutil.cleanup_connections("src:out_1")
        self.assertEqual(mock_disconnect.call_count, 2)

if __name__ == "__main__":
    unittest.main()
