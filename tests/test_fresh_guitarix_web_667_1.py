import unittest
from datetime import datetime, timedelta, timezone

from forge_core.services.portainer_approval import create_redeploy_request


class TestRedeployRequestExpiryUTC(unittest.TestCase):
    def test_expiration_is_stored_as_utc(self):
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)

        naive_expiry = datetime(2031, 2, 3, 4, 5, 6)
        naive_request = create_redeploy_request(
            "stack", "endpoint", "revision", "requester", naive_expiry, now=now
        )
        self.assertIsNotNone(naive_request.expires_at.tzinfo)
        self.assertEqual(naive_request.expires_at.utcoffset(), timedelta(0))
        self.assertEqual(
            (
                naive_request.expires_at.year,
                naive_request.expires_at.month,
                naive_request.expires_at.day,
                naive_request.expires_at.hour,
                naive_request.expires_at.minute,
                naive_request.expires_at.second,
                naive_request.expires_at.microsecond,
            ),
            (
                naive_expiry.year,
                naive_expiry.month,
                naive_expiry.day,
                naive_expiry.hour,
                naive_expiry.minute,
                naive_expiry.second,
                naive_expiry.microsecond,
            ),
        )

        non_utc_expiry = datetime(
            2031, 2, 3, 9, 5, 6, tzinfo=timezone(timedelta(hours=5))
        )
        non_utc_request = create_redeploy_request(
            "stack", "endpoint", "revision", "requester", non_utc_expiry, now=now
        )
        self.assertEqual(
            non_utc_request.expires_at,
            datetime(2031, 2, 3, 4, 5, 6, tzinfo=timezone.utc),
        )
        self.assertIs(non_utc_request.expires_at.tzinfo, timezone.utc)
        self.assertEqual(non_utc_request.expires_at.utcoffset(), timedelta(0))

        comparison = non_utc_request.expires_at < datetime.now(timezone.utc)
        self.assertIn(comparison, (True, False))


if __name__ == "__main__":
    unittest.main()
