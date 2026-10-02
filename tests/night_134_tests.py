import unittest
from unittest.mock import patch, MagicMock
import jackutil


class TestJackConnectionRetry(unittest.TestCase):
    @patch("jackutil.time.sleep")
    @patch("jackutil.subprocess.run")
    def test_connect_retries_on_failure_then_succeeds(self, mock_run, mock_sleep):
        fail_res = MagicMock(returncode=1)
        success_res = MagicMock(returncode=0)
        mock_run.side_effect = [fail_res, fail_res, success_res]

        result = jackutil.connect("src:out", "dst:in", max_retries=3, backoff_factor=0.01)

        self.assertTrue(result)
        self.assertEqual(mock_run.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        mock_sleep.assert_any_call(0.01)
        mock_sleep.assert_any_call(0.02)

    @patch("jackutil.time.sleep")
    @patch("jackutil.subprocess.run")
    def test_connect_fails_after_max_retries(self, mock_run, mock_sleep):
        fail_res = MagicMock(returncode=1)
        mock_run.return_value = fail_res

        result = jackutil.connect("src:out", "dst:in", max_retries=3, backoff_factor=0.01)

        self.assertFalse(result)
        self.assertEqual(mock_run.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
