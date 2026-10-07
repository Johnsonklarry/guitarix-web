"""Root confinement for the file routes (#139).

These tests drive the real routes: the Flask test client for the GETs and the
multipart POST, and app.wsgi_app with a hand-built environ when the path has to
reach the view with its ".." segment intact (the test client may tidy that away
before the view ever sees it).

Importing app.py needs flask and flask_socketio. If they are missing that is an
environment problem rather than a property of the code under test, so the whole
module is skipped instead of erroring out.
"""

import io
import os
import shutil
import tempfile
import unittest

try:
    import app as app_module
except ImportError as exc:                      # pragma: no cover
    app_module = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


# The first line of app.py: if a traversal ever served it, we would see this.
APP_PY_FIRST_LINE = b"import contextvars"


@unittest.skipIf(app_module is None,
                 "app.py cannot be imported (needs flask and flask_socketio): %r"
                 % (IMPORT_ERROR,))
class ConfinedFileRoutesTest(unittest.TestCase):
    """Every route that turns a request name into a path stays inside its root."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="confined-")
        self.rec_dir = os.path.join(self.tmp, "recordings")
        self.backing_dir = os.path.join(self.tmp, "backing")
        os.makedirs(self.rec_dir)
        os.makedirs(self.backing_dir)

        self._rec_dir = app_module.rec.dir
        self._backing_dir = app_module.backing.dir
        app_module.rec.dir = self.rec_dir
        app_module.backing.dir = self.backing_dir

        with open(os.path.join(self.rec_dir, "take.wav"), "w") as fh:
            fh.write("not really audio")
        with open(os.path.join(self.backing_dir, "track.mp3"), "w") as fh:
            fh.write("backing track bytes")

        # An upload has to be refused before it reaches the library, so watch
        # the call rather than the file that would have landed.
        self.saved = []
        self._save_upload = app_module.backing.save_upload

        def recording_save_upload(filename, stream):
            self.saved.append(filename)
            target = os.path.join(self.backing_dir, os.path.basename(filename or ""))
            with open(target, "wb") as fh:
                fh.write(stream.read())
            return os.path.basename(filename)

        app_module.backing.save_upload = recording_save_upload

        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.backing.save_upload = self._save_upload
        app_module.rec.dir = self._rec_dir
        app_module.backing.dir = self._backing_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------- _confined

    def test_confined_returns_a_file_inside_the_root(self):
        self.assertEqual(app_module._confined(self.rec_dir, "take.wav"),
                         os.path.join(self.rec_dir, "take.wav"))

    def test_confined_refuses_names_that_leave_the_root(self):
        for name in ("../app.py", "../../etc/passwd", "sub/../take.wav", "..", "", None):
            with self.subTest(name=name):
                self.assertIsNone(app_module._confined(self.rec_dir, name))

    def test_confined_refuses_a_name_that_is_not_there(self):
        self.assertIsNone(app_module._confined(self.rec_dir, "missing.wav"))

    def test_confined_can_check_a_name_that_does_not_exist_yet(self):
        # An upload names the file it is about to write; it is the *name* that
        # has to be confined, not whether the file is already there.
        self.assertEqual(app_module._confined(self.backing_dir, "new.mp3", must_exist=False),
                         os.path.join(self.backing_dir, "new.mp3"))
        self.assertIsNone(app_module._confined(self.backing_dir, "new.mp3"))

    # ---------------------------------------------------------- serving files

    def test_recording_is_served(self):
        response = self.client.get("/recordings/take.wav")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"not really audio")

    def test_backing_track_is_served(self):
        response = self.client.get("/backing/track.mp3")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"backing track bytes")

    def test_traversal_gets_are_refused(self):
        for path in ("/recordings/../app.py", "/backing/../../etc/passwd"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(APP_PY_FIRST_LINE, response.data)

    def test_encoded_traversal_gets_are_refused(self):
        for path in ("/recordings/%2e%2e/app.py", "/backing/%2e%2e/%2e%2e/etc/passwd"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(APP_PY_FIRST_LINE, response.data)

    def test_raw_path_info_traversal_is_refused(self):
        # Straight to the WSGI app, with the ".." still in PATH_INFO, so no
        # client-side tidying can hide what the view does with it.
        environ = {
            "REQUEST_METHOD": "GET",
            "SCRIPT_NAME": "",
            "PATH_INFO": "/recordings/../app.py",
            "QUERY_STRING": "",
            "CONTENT_TYPE": "",
            "CONTENT_LENGTH": "0",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "80",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "HTTP_HOST": "localhost",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(b""),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        captured = {}

        def start_response(status, headers, exc_info=None):
            captured["status"] = status
            return lambda chunk: None

        chunks = app_module.app.wsgi_app(environ, start_response)
        try:
            body = b"".join(chunks)
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()

        self.assertTrue(captured["status"].startswith("404"), captured["status"])
        self.assertNotIn(APP_PY_FIRST_LINE, body)

    # --------------------------------------------------------------- uploads

    def test_upload_with_traversal_is_refused(self):
        response = self.client.post(
            "/backing",
            data={"file": (io.BytesIO(b"payload"), "../escape.mp3")},
            content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)
        self.assertIs(response.get_json()["ok"], False)
        self.assertEqual(self.saved, [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.mp3")))
        self.assertEqual(sorted(os.listdir(self.tmp)), ["backing", "recordings"])

    def test_upload_with_plain_name_is_accepted(self):
        response = self.client.post(
            "/backing",
            data={"file": (io.BytesIO(b"payload"), "new.mp3")},
            content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.get_json()["ok"], True)
        self.assertEqual(self.saved, ["new.mp3"])
        self.assertTrue(os.path.isfile(os.path.join(self.backing_dir, "new.mp3")))
        self.assertEqual(sorted(os.listdir(self.tmp)), ["backing", "recordings"])


if __name__ == "__main__":
    unittest.main()
