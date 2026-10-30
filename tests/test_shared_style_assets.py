#!/usr/bin/env python3
"""Regression coverage for vendored shared Forge style assets."""

import contextlib
import html
import importlib
import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import unquote, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAKES = os.path.join(HERE, "fakes")
STUBS = os.path.join(HERE, "stubs")
ALIAS = "gxweb_shared_style_assets_app"


def _no_background_task(self, target, *args, **kwargs):
    return None


def _no_thread_start(self, *args, **kwargs):
    raise AssertionError("threads are forbidden while importing app.py")


def _project_module(module):
    filename = getattr(module, "__file__", None)
    return (filename and
            os.path.dirname(os.path.realpath(filename)) == os.path.realpath(ROOT))


def _evict_project_modules():
    saved = {}
    for name, module in list(sys.modules.items()):
        if _project_module(module):
            saved[name] = module
            del sys.modules[name]
    return saved


def _restore_project_modules(saved):
    for name, module in list(sys.modules.items()):
        if name == ALIAS or _project_module(module):
            del sys.modules[name]
    sys.modules.update(saved)


class SharedStyleAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._stack = contextlib.ExitStack()
        try:
            cls._configure()
        except BaseException:
            cls._stack.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls._stack.close()

    @classmethod
    def _configure(cls):
        stack = cls._stack
        fake_bin = os.path.join(FAKES, "bin")
        stub_file = os.path.join(STUBS, "flask_socketio.py")
        if not os.path.isdir(fake_bin) or not os.listdir(fake_bin):
            raise RuntimeError("tests/fakes/bin is required and must not be empty")
        if not os.path.isfile(stub_file):
            raise RuntimeError("tests/stubs/flask_socketio.py is required")

        for name in os.listdir(fake_bin):
            path = os.path.join(fake_bin, name)
            if os.path.isfile(path):
                os.chmod(path, os.stat(path).st_mode | 0o111)

        sandbox = tempfile.mkdtemp(prefix="gxweb-style-")
        stack.callback(shutil.rmtree, sandbox, True)
        home = os.path.join(sandbox, "home")
        config = os.path.join(home, ".config")
        data = os.path.join(home, ".local", "share")
        state = os.path.join(home, ".local", "state")
        cache = os.path.join(home, ".cache")
        tmp = os.path.join(sandbox, "tmp")
        jack = os.path.join(sandbox, "jack")
        recordings = os.path.join(home, "recordings")
        backing = os.path.join(home, "backing")
        banks = os.path.join(config, "guitarix", "banks")
        for directory in (home, config, data, state, cache, tmp, jack,
                          recordings, backing, banks):
            os.makedirs(directory, exist_ok=True)

        old_cwd = os.getcwd()
        os.chdir(sandbox)
        stack.callback(os.chdir, old_cwd)

        env = {
            "HOME": home,
            "XDG_CONFIG_HOME": config,
            "XDG_DATA_HOME": data,
            "XDG_STATE_HOME": state,
            "XDG_CACHE_HOME": cache,
            "TMPDIR": tmp,
            "FAKE_JACK_DIR": jack,
            "PATH": fake_bin + os.pathsep + os.environ.get("PATH", ""),
            "GX_HOST": "127.0.0.1",
            "GX_PORT": "1",
            "GX_DEMO_ONLY": "",
            "GX_BROADCAST": "",
            "GX_PASSWORD": "",
            "GX_REC_FEED": os.path.join(sandbox, "rec-feed.json"),
            "GX_SECRET_KEY": "shared-style-assets-test",
            "GX_BANK_DIR": banks,
            "GX_BANKS_DIR": banks,
            "GX_PRESETS_DIR": banks,
        }
        stack.enter_context(mock.patch.dict(os.environ, env, clear=False))

        def blocked_socket(*args, **kwargs):
            raise AssertionError("network access is forbidden in this test")

        stack.enter_context(mock.patch(
            "socket.create_connection", new=blocked_socket))
        stack.enter_context(mock.patch.object(
            socket.socket, "connect", new=blocked_socket))
        stack.enter_context(mock.patch.object(
            socket.socket, "connect_ex", new=blocked_socket))
        stack.enter_context(mock.patch(
            "threading.Thread.start", new=_no_thread_start))

        real_popen = subprocess.Popen
        fake_root = os.path.realpath(fake_bin)

        def fake_only_popen(args, *a, **kw):
            executable = kw.get("executable")
            if not executable:
                executable = args[0] if isinstance(args, (list, tuple)) and args else args
            resolved = shutil.which(os.fsdecode(executable))
            if not resolved or os.path.commonpath(
                    [os.path.realpath(resolved), fake_root]) != fake_root:
                raise AssertionError("non-fake subprocess blocked: %r" % (executable,))
            return real_popen(args, *a, **kw)

        stack.enter_context(mock.patch(
            "subprocess.Popen", new=fake_only_popen))

        old_path = list(sys.path)
        sys.path[:0] = [STUBS, ROOT]
        stack.callback(lambda: sys.path.__setitem__(slice(None), old_path))

        saved_project = _evict_project_modules()
        stack.callback(_restore_project_modules, saved_project)

        saved_socketio = sys.modules.pop("flask_socketio", None)

        def restore_socketio():
            sys.modules.pop("flask_socketio", None)
            if saved_socketio is not None:
                sys.modules["flask_socketio"] = saved_socketio

        stack.callback(restore_socketio)

        fake_socketio = importlib.import_module("flask_socketio")
        if os.path.realpath(getattr(fake_socketio, "__file__", "")) != os.path.realpath(stub_file):
            raise RuntimeError("the test flask_socketio stub is not selected")
        stack.enter_context(mock.patch.object(
            fake_socketio.SocketIO, "start_background_task",
            new=_no_background_task, create=True))

        spec = importlib.util.spec_from_file_location(ALIAS, os.path.join(ROOT, "app.py"))
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load app.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[ALIAS] = module
        stack.callback(sys.modules.pop, ALIAS, None)
        spec.loader.exec_module(module)

        cls._assert_private_paths(module, sandbox)
        cls.app_module = module
        cls.client = module.app.test_client()
        cls.vendor = os.path.join(ROOT, "forge_core", "static", "style")

    @staticmethod
    def _assert_private_paths(module, sandbox):
        root = os.path.realpath(sandbox)

        def private(path, label):
            path = os.path.realpath(os.path.expanduser(path))
            if os.path.commonpath([path, root]) != root:
                raise RuntimeError("%s escapes the disposable sandbox: %s" %
                                   (label, path))

        if os.path.realpath(os.getcwd()) != root:
            raise RuntimeError("relative application paths are not sandboxed")
        private(module.rec.dir, "recordings")
        private(module.backing.dir, "backing")

        # Only writable runtime locations are guarded. Module metadata
        # (__file__, __cached__, __spec__) and read-only configuration
        # constants legitimately live in the source snapshot, not the
        # sandbox, and are not writable state.
        guarded = {
            "recorder": ("RECORDINGS_DIR",),
            "backing": ("BACKING_DIR",),
            "presets_io": ("BANK_DIR", "BANKS_DIR", "PRESETS_DIR"),
        }
        for name, keys in guarded.items():
            imported = sys.modules.get(name)
            if imported is None:
                continue
            for key in keys:
                value = getattr(imported, key, None)
                if isinstance(value, str):
                    private(value, "%s.%s" % (name, key))

    def imported_styles(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        found = []
        for href in re.findall(r'\bhref=["\']([^"\']+)["\']',
                               response.get_data(as_text=True), re.I):
            href = html.unescape(href)
            path = unquote(urlsplit(href).path)
            marker = "/style/"
            if marker in path:
                name = path.split(marker, 1)[1]
                if name and name not in found:
                    found.append(name)
        self.assertTrue(found, "index must import at least one shared style")
        return found

    def style_url(self, name):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        pattern = r'href="([^"]*style/%s[^"]*)"' % re.escape(name)
        match = re.search(pattern, page.get_data(as_text=True))
        self.assertIsNotNone(match, "index must import style/%s" % name)
        return html.unescape(match.group(1))

    def test_imported_styles_are_served_as_exact_vendored_css(self):
        for name in self.imported_styles():
            with self.subTest(name=name):
                source = os.path.realpath(os.path.join(self.vendor, name))
                self.assertEqual(os.path.commonpath(
                    [source, os.path.realpath(self.vendor)]),
                    os.path.realpath(self.vendor))
                with open(source, "rb") as f:
                    expected = f.read()
                response = self.client.get(self.style_url(name))
                self.addCleanup(response.close)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, expected)
                self.assertEqual(response.mimetype, "text/css")

    def test_style_revision_tracks_the_file_that_is_served(self):
        name = self.imported_styles()[0]
        source = os.path.join(self.vendor, name)
        original = os.stat(source)
        before = self.style_url(name)
        try:
            os.utime(source, ns=(original.st_atime_ns,
                                 original.st_mtime_ns + 2_000_000_000))
            after = self.style_url(name)
            self.assertNotEqual(before, after)
            response = self.client.get(after)
            self.addCleanup(response.close)
            with open(source, "rb") as f:
                self.assertEqual(response.data, f.read())
        finally:
            os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))

    def test_style_route_cannot_escape_its_canonical_root(self):
        blocked = (
            "/static/style/../app.js",
            "/static/style/%2e%2e%2fapp.js",
            "/static/style/%2e%2e%5capp.js",
            "/static/style/cards.css/..",
            "/static/style/not-vendored.css",
        )
        for path in blocked:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_existing_non_shared_static_assets_remain_flask_static_assets(self):
        source = os.path.join(self.app_module.app.static_folder, "app.js")
        with open(source, "rb") as f:
            expected = f.read()
        response = self.client.get("/static/app.js")
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, expected)
        self.assertNotEqual(urlsplit(self.style_url(
            self.imported_styles()[0])).path, "/static/app.js")


if __name__ == "__main__":
    unittest.main()

