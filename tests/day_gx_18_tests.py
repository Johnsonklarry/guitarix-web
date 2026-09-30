#!/usr/bin/env python3
"""
gx-18 (Epic #9): the Socket.IO client is served from static/, not a CDN.

    python3 tests/day_gx_18_tests.py

The pages must reference the local file via asset('socket.io.min.js'); the CDN
URL may only appear as a document.write fallback for a checkout where
tools/vendor_socketio.py has not been run yet. If static/socket.io.min.js is
present it must match the pin. No network is used.
"""

import importlib.util
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOCAL_TAG = "{{ asset('socket.io.min.js') }}"


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def load_vendor():
    spec = importlib.util.spec_from_file_location(
        "vendor_socketio", os.path.join(ROOT, "tools", "vendor_socketio.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PagesUseLocalClient(unittest.TestCase):
    def check(self, name):
        html = read("templates", name)
        srcs = re.findall(r'<script\s+src="([^"]+)"', html)
        self.assertIn(LOCAL_TAG, srcs, name + " must load the local socket.io client")
        for m in re.finditer(r"cdn\.socket\.io", html):
            line_start = html.rfind("\n", 0, m.start()) + 1
            self.assertIn("document.write", html[line_start:m.start()],
                          name + ": the CDN may only be a document.write fallback")
        # the local tag comes before app code so io() exists when it runs
        js = "app.js" if name == "index.html" else "broadcast.js"
        self.assertLess(html.index(LOCAL_TAG), html.index("asset('%s')" % js))

    def test_index(self):
        self.check("index.html")

    def test_broadcast(self):
        self.check("broadcast.html")


class VendorScript(unittest.TestCase):
    def test_pin_matches_url_and_dest(self):
        v = load_vendor()
        self.assertIn("/" + v.VERSION + "/", v.URL)
        self.assertTrue(v.EXPECTED_SHA384.startswith("sha384-"))
        self.assertTrue(v.DEST.endswith(os.path.join("static", "socket.io.min.js")))

    def test_problems_accepts_good_and_rejects_bad(self):
        v = load_vendor()
        good = (b"/*! Socket.IO v" + v.VERSION.encode() + b" */" + b"x" * 40000)
        self.assertEqual(v.problems(good, v.sri384(good)), [])
        self.assertTrue(v.problems(good))                      # wrong pin
        self.assertTrue(v.problems(b"tiny", v.sri384(b"tiny")))  # too small
        wrong = b"/*! Socket.IO v0.0.1 */" + b"x" * 40000
        self.assertTrue(v.problems(wrong, v.sri384(wrong)))     # wrong version

    def test_vendored_file_matches_pin_if_present(self):
        v = load_vendor()
        if not os.path.exists(v.DEST):
            self.skipTest("static/socket.io.min.js not vendored yet; run tools/vendor_socketio.py")
        with open(v.DEST, "rb") as f:
            self.assertEqual(v.problems(f.read()), [])


if __name__ == "__main__":
    unittest.main()
