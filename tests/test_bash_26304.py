#!/usr/bin/env python3
"""Regression test for issue #26304: the WSGI entrypoint enforces GX_DEMO_ONLY.

A service does not import app.py, it imports the WSGI entrypoint module.  If
that module builds its own application -- or imports the real one too late --
then the module-level demo guard never reaches the app a request actually
hits, and demo mode quietly stops protecting the rig.  So: import the
entrypoint the way the service unit does, and drive the object it exposes.
"""

import atexit
import importlib
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                 # the directory the service runs from
STUBS = os.path.join(HERE, "stubs")          # same import root the server test uses

# Anything the app writes while it is being imported stays inside a temp dir...
_TMP_HOME = tempfile.mkdtemp(prefix="gxweb-wsgi-")
atexit.register(shutil.rmtree, _TMP_HOME, ignore_errors=True)
os.environ["HOME"] = _TMP_HOME
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP_HOME, ".config")
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP_HOME, ".local", "share")

# ...and the demo flag is set before the import, because app.py reads it at
# import time.  That is the only thing the service unit does differently.
os.environ["GX_DEMO_ONLY"] = "1"

# the same way the server does it: repo root (and the stub dir, when present)
# ahead of everything else, so the real tree is what gets imported
sys.path[:0] = [p for p in (STUBS, ROOT) if os.path.isdir(p)]


def _exposed_app(module):
    """The Flask application the entrypoint exposes, whatever it is called."""
    from flask import Flask
    for name in ("app", "application", "wsgi_app"):
        candidate = getattr(module, name, None)
        if isinstance(candidate, Flask):
            return candidate
    for candidate in list(vars(module).values()):
        if isinstance(candidate, Flask):
            return candidate
    return None


class WsgiEntrypointDemoOnlyTest(unittest.TestCase):
    """GX_DEMO_ONLY=1 has to reach the app through the WSGI entrypoint."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.entrypoint = importlib.import_module("wsgi")
        except ImportError as exc:
            raise AssertionError("the WSGI entrypoint cannot be imported: %s" % exc)
        cls.app = _exposed_app(cls.entrypoint)
        if cls.app is None:
            raise AssertionError("the WSGI entrypoint exposes no Flask application")

    def test_entrypoint_exposes_the_guarded_app(self):
        # the very instance app.py's module-level guard was applied to
        app_module = sys.modules.get("app")
        self.assertIsNotNone(app_module, "the entrypoint never imported app.py")
        self.assertIs(self.app, app_module.app,
                      "the entrypoint exposes a different app than the guarded one")
        flag = getattr(self.app, "DEMO_ONLY", None)
        if flag is None:
            flag = getattr(app_module, "DEMO_ONLY", None)
        self.assertTrue(flag, "GX_DEMO_ONLY=1 did not reach the demo guard")

    def test_rig_routes_are_refused(self):
        with self.app.test_client() as client:
            for url in ("/api/state", "/monitor.mp3"):
                response = client.get(url)
                self.assertEqual(response.status_code, 403,
                                 "%s was not refused (got %s)" % (url, response.status_code))

    def test_root_serves_the_demo_page(self):
        with self.app.test_client() as client:
            page = client.get("/").get_data(as_text=True)
        self.assertIn("demo.js", page)
        self.assertNotIn("socket.io", page)


if __name__ == "__main__":
    unittest.main()
