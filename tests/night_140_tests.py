"""
Regression tests for issue #116 part 2: authorized take listing, upload, and
download through the canonical connector and proxy.

Run with: python3 tests/night_140_tests.py

These fail against the pre-fix filebrowser, which reached for the filesystem
directly and checked authorization on the listing only -- so an unauthorized
download and an unauthorized upload both went through.
"""

import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import filebrowser as fb  # noqa: E402


class FakeConnector:
    """Stands in for the canonical connector: one root, one token."""

    def __init__(self, root, token="good-token"):
        self.root = os.path.abspath(root)
        self.token = token
        self.stored = {}

    def authorized(self, token):
        return token == self.token

    def resolve(self, name):
        path = os.path.abspath(os.path.join(self.root, os.path.basename(name or "")))
        if os.path.dirname(path) != self.root or not os.path.isfile(path):
            return None
        return path

    def listing(self):
        return [{"name": n} for n in sorted(os.listdir(self.root))
                if os.path.isfile(os.path.join(self.root, n))]

    def store(self, name, stream):
        safe = os.path.basename(name or "take")
        with open(os.path.join(self.root, safe), "wb") as f:
            f.write(stream.read())
        self.stored[safe] = True
        return safe


class FakeProxy:
    def __init__(self, connector):
        self.connector = connector

    def listing(self):
        return self.connector.listing()


class FilebrowserTakesAuthorizationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        with open(os.path.join(self.root, "Attempt 1.wav"), "wb") as f:
            f.write(b"RIFF....WAVE")

        self.conn = FakeConnector(self.root)
        fb.canonical_connector = lambda root=None: self.conn
        fb.canonical_proxy = lambda connector=None: FakeProxy(connector)
        fb.TAKES_ROOT = self.root

        self.app = fb.filebrowser
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(self.app)
        self.client = app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    # ---------------------------------------------------------- authorized

    def test_authorized_list(self):
        r = self.client.get("/takes", headers={"Authorization": "Bearer good-token"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual([t["name"] for t in r.get_json()["takes"]], ["Attempt 1.wav"])

    def test_authorized_download(self):
        r = self.client.get("/takes/Attempt 1.wav",
                            headers={"Authorization": "Bearer good-token"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, b"RIFF....WAVE")

    def test_authorized_upload(self):
        r = self.client.post(
            "/takes",
            headers={"Authorization": "Bearer good-token"},
            data={"file": (io.BytesIO(b"RIFFnew"), "Attempt 2.wav")},
            content_type="multipart/form-data")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "Attempt 2.wav")))

    # -------------------------------------------------------- unauthorized

    def test_unauthorized_list(self):
        r = self.client.get("/takes", headers={"Authorization": "Bearer bad-token"})
        self.assertEqual(r.status_code, 401)

    def test_unauthorized_download(self):
        r = self.client.get("/takes/Attempt 1.wav",
                            headers={"Authorization": "Bearer bad-token"})
        self.assertEqual(r.status_code, 401)

    def test_unauthorized_upload(self):
        r = self.client.post(
            "/takes",
            headers={"Authorization": "Bearer bad-token"},
            data={"file": (io.BytesIO(b"RIFFnew"), "Attempt 3.wav")},
            content_type="multipart/form-data")
        self.assertEqual(r.status_code, 401)
        self.assertFalse(os.path.isfile(os.path.join(self.root, "Attempt 3.wav")))

    # ------------------------------------------------------------ the root

    def test_download_outside_root_denied(self):
        r = self.client.get("/takes/../../etc/passwd",
                            headers={"Authorization": "Bearer good-token"})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
