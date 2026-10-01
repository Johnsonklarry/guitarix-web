#!/usr/bin/env python3
"""
Regression tests for the login/logout endpoints (part 1 of #16).

    python3 tests/night_143_tests.py
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAKES = os.path.join(HERE, "fakes")

os.environ["FAKE_JACK_DIR"] = tempfile.mkdtemp(prefix="gxweb-jack-")
if os.path.isdir(os.path.join(FAKES, "bin")):
    os.environ["PATH"] = os.path.join(FAKES, "bin") + os.pathsep + os.environ["PATH"]
os.environ.setdefault("GX_PORT", "1")
sys.path[:0] = [os.path.join(HERE, "stubs"), ROOT]

import recorder                      # noqa: E402
import backing as backing_mod        # noqa: E402
recorder.RECORDINGS_DIR = tempfile.mkdtemp(prefix="gxweb-rec-")
backing_mod.BACKING_DIR = tempfile.mkdtemp(prefix="gxweb-backing-")

import app as A                      # noqa: E402


class AuthEndpoints(unittest.TestCase):
    def setUp(self):
        os.environ["GX_PASSWORD"] = "s3cret"
        self.client = A.app.test_client()

    def tearDown(self):
        os.environ.pop("GX_PASSWORD", None)

    def test_valid_password_logs_in_and_sets_cookie(self):
        r = self.client.post("/login", json={"password": "s3cret"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(h.startswith("session=")
                            for h in r.headers.getlist("Set-Cookie")))
        with self.client.session_transaction() as s:
            self.assertTrue(s.get("authenticated"))

    def test_invalid_password_refused(self):
        r = self.client.post("/login", json={"password": "nope"})
        self.assertEqual(r.status_code, 401)
        with self.client.session_transaction() as s:
            self.assertFalse(s.get("authenticated"))

    def test_missing_password_refused(self):
        self.assertEqual(self.client.post("/login", json={}).status_code, 401)

    def test_unset_password_refuses_everything(self):
        os.environ.pop("GX_PASSWORD", None)
        self.assertEqual(self.client.post("/login", json={"password": ""}).status_code, 401)

    def test_logout_clears_session(self):
        self.client.post("/login", json={"password": "s3cret"})
        r = self.client.post("/logout")
        self.assertEqual(r.status_code, 200)
        with self.client.session_transaction() as s:
            self.assertFalse(s.get("authenticated"))


if __name__ == "__main__":
    unittest.main()
