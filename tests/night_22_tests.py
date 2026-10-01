#!/usr/bin/env python3
"""
Night 22: the app must be launchable by gunicorn, not just `python3 app.py`.

    python3 tests/night_22_tests.py

Under gunicorn `if __name__ == "__main__"` never runs, so the guitarix
connection and the flusher/ticker loops were never started: the page loaded and
stayed dead. `wsgi.py` is the gunicorn entry point and calls
`app.start_services()`, which does what the `__main__` block used to do, once.

The environment is set BEFORE importing the app (demo-only, so guitarix is
never contacted). No gunicorn process is started; where gunicorn is installed,
its own loader is used to check that `wsgi:app` imports.
"""

import importlib.util
import os
import socket
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


os.environ["GX_DEMO_ONLY"] = "1"
os.environ["GX_PORT"] = str(free_port())          # nothing is listening there
os.environ["FAKE_JACK_DIR"] = tempfile.mkdtemp(prefix="gxweb-jack-")
os.environ["PATH"] = (os.path.join(HERE, "fakes", "bin") + os.pathsep
                      + os.environ["PATH"])
sys.path[:0] = [os.path.join(HERE, "stubs"), ROOT]

import app as A                      # noqa: E402


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class TestGunicornLaunch(unittest.TestCase):
    def setUp(self):
        saved = A._services_started if hasattr(A, "_services_started") else False
        self.addCleanup(setattr, A, "_services_started", saved)
        A._services_started = False

    def test_wsgi_exposes_the_flask_app(self):
        import wsgi
        self.assertIs(wsgi.app, A.app)

    def test_gunicorn_loader_imports_wsgi_app(self):
        if importlib.util.find_spec("gunicorn") is None:
            self.skipTest("gunicorn is not installed here")
        try:
            from gunicorn.util import import_app
        except ImportError:
            self.skipTest("gunicorn cannot be imported on this platform")
        self.assertIs(import_app("wsgi:app"), A.app)

    def test_services_start_once(self):
        with mock.patch.object(A, "DEMO_ONLY", False), \
                mock.patch.object(A, "rpc") as rpc, \
                mock.patch.object(A, "socketio") as sio:
            A.start_services()
            A.start_services()
        rpc.start.assert_called_once_with()
        self.assertEqual(sio.start_background_task.call_count, 2)
        sio.start_background_task.assert_any_call(A.flusher)
        sio.start_background_task.assert_any_call(A.ticker)

    def test_demo_only_starts_nothing(self):
        with mock.patch.object(A, "DEMO_ONLY", True), \
                mock.patch.object(A, "rpc") as rpc, \
                mock.patch.object(A, "socketio") as sio:
            A.start_services()
        rpc.start.assert_not_called()
        sio.start_background_task.assert_not_called()

    def test_requirements_and_docs_name_the_launch_command(self):
        self.assertIn("gunicorn", read("requirements.txt"))
        readme = read("README.md")
        self.assertIn("gunicorn -k gthread -w 1", readme)
        self.assertIn("wsgi:app", readme)
        self.assertIn("wsgi:app", read("guitarix-web.service"))


if __name__ == "__main__":
    unittest.main()
