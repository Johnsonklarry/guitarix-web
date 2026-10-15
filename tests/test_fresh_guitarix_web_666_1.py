import unittest
from datetime import datetime, timedelta, timezone

from forge_core.services.portainer_approval import create_redeploy_request


class RedeployRequestValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        self.details = {
            "stack": "guitarix",
            "endpoint": "production",
            "revision": "aBc123F",
            "requester": "operator",
        }

    def create(self, *, revision=None, expires_at=None, now=None):
        details = dict(self.details)
        if revision is not None:
            details["revision"] = revision
        return create_redeploy_request(
            **details,
            expires_at=expires_at,
            now=self.now if now is None else now,
        )

    def test_valid_sha_and_ttls(self):
        for minutes in (10, 15):
            with self.subTest(minutes=minutes):
                expiry = self.now + timedelta(minutes=minutes)
                request = self.create(expires_at=expiry)
                self.assertEqual(request.revision, "aBc123F")
                self.assertEqual(request.expires_at, expiry)

        full_sha = "0123456789abcdef" * 2 + "01234567"
        self.assertEqual(
            self.create(revision=full_sha, expires_at=self.now + timedelta(minutes=10)).revision,
            full_sha,
        )

    def test_invalid_revisions(self):
        for revision in ("latest", "https://x/y", "../../other", "zzzz123"):
            with self.subTest(revision=revision):
                with self.assertRaises(ValueError):
                    self.create(revision=revision, expires_at=self.now + timedelta(minutes=10))

    def test_expiry_outside_allowed_window(self):
        for offset in (
            timedelta(minutes=15, seconds=1),
            timedelta(days=365 * 3),
            timedelta(0),
            -timedelta(seconds=1),
        ):
            with self.subTest(offset=offset):
                with self.assertRaises(ValueError):
                    self.create(expires_at=self.now + offset)

    def test_naive_and_aware_datetimes_use_utc(self):
        naive_now = self.now.replace(tzinfo=None)
        naive_expiry = naive_now + timedelta(minutes=15)
        offset_zone = timezone(timedelta(hours=2))
        aware_expiry = (self.now + timedelta(minutes=15)).astimezone(offset_zone)

        self.assertEqual(
            self.create(expires_at=naive_expiry, now=naive_now).expires_at,
            naive_expiry,
        )
        self.assertEqual(
            self.create(expires_at=aware_expiry, now=naive_now).expires_at,
            aware_expiry,
        )
        self.assertEqual(
            self.create(expires_at=naive_expiry, now=self.now).expires_at,
            naive_expiry,
        )

        with self.assertRaises(ValueError):
            self.create(
                expires_at=(self.now + timedelta(minutes=15, seconds=1)).astimezone(offset_zone),
                now=naive_now,
            )


if __name__ == "__main__":
    unittest.main()
