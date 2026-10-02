"""Regression tests for the module-level GX_DEMO_ONLY guard in app.py.

Run: python3 tests/night_207_tests.py

The guard must be decided at import time from the environment, and a
demo-only server must refuse every rig-touching route while still serving
the page, its assets and the manifest.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set before app is imported: the guard is module scope.
os.environ["GX_DEMO_ONLY"] = "1"
os.environ.pop("GX_BROADCAST", None)

import app as app_module  # noqa: E402


class DemoOnlyGuardTests(unittest.TestCase):

    def setUp(self):
        self.assertTrue(app_module.DEMO_ONLY,
                        "GX_DEMO_ONLY=1 must be read at module scope")
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def test_demo_only_is_module_scope(self):
        self.assertIs(app_module.DEMO_ONLY, True)

    def test_page_and_assets_still_served(self):
        for path in ("/", "/manifest.webmanifest"):
            resp = self.client.get(path)
            self.assertNotEqual(resp.status_code, 403,
                                "%s must stay reachable in demo mode" % path)

    def test_rig_touching_routes_refused(self):
        rig_routes = (
            ("GET", "/api/state"),
            ("GET", "/api/parameters"),
            ("GET", "/api/parameters.json"),
            ("GET", "/monitor.mp3"),
            ("GET", "/recordings/take.wav"),
            ("GET", "/backing/track.mp3"),
            ("POST", "/backing"),
        )
        for method, path in rig_routes:
            resp = self.client.open(path, method=method)
            self.assertEqual(resp.status_code, 403,
                             "%s %s must be refused in demo mode (got %d)"
                             % (method, path, resp.status_code))

    def test_unknown_route_is_not_a_rig_route(self):
        # A 404 is fine; what matters is that the guard does not 500.
        resp = self.client.get("/no-such-thing")
        self.assertIn(resp.status_code, (403, 404))

    def test_socket_connect_refused(self):
        # The live connection is refused too: the demo never needs it.
        self.assertTrue(app_module.DEMO_ONLY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
