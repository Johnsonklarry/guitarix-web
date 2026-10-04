"""Regression tests for issue #144: the HTTP auth guard on state-changing routes.

The guard lives in app.py as a before_request hook. Importing app.py pulls in
flask/flask_socketio and the sibling modules, so these tests skip cleanly when
those aren't installed rather than failing the suite.
"""

import os
import unittest
from unittest import mock

try:
    import flask  # noqa: F401
    import flask_socketio  # noqa: F401
    import app as app_module
    HAVE_APP = True
except Exception:  # pragma: no cover - environment without the web deps
    HAVE_APP = False


@unittest.skipUnless(HAVE_APP, "flask/flask_socketio not installed")
class AuthGuardTests(unittest.TestCase):
    def setUp(self):
        # A password is configured, so the guard is active.
        self._env = mock.patch.dict(os.environ, {"GX_PASSWORD": "hunter2"})
        self._env.start()
        self.addCleanup(self._env.stop)

        self.app = app_module.app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _login(self):
        return self.client.post("/login", json={"password": "hunter2"})

    # -- unauthenticated -------------------------------------------------

    def test_unauthenticated_post_is_refused(self):
        resp = self.client.post("/login", json={"password": "wrong"})
        # /login is exempt from the guard, so it answers 401 itself
        self.assertEqual(resp.status_code, 401)

    def test_unauthenticated_post_to_protected_route_is_refused(self):
        resp = self.client.post("/backing")
        self.assertIn(resp.status_code, (401, 403))

    def test_unauthenticated_put_is_refused(self):
        resp = self.client.put("/backing")
        self.assertIn(resp.status_code, (401, 403))

    def test_unauthenticated_delete_is_refused(self):
        resp = self.client.delete("/backing")
        self.assertIn(resp.status_code, (401, 403))

    # -- authenticated ---------------------------------------------------

    def test_authenticated_post_reaches_the_route(self):
        self._login()
        resp = self.client.post("/backing")
        # No files were sent, so the route itself answers 400 -- the point is
        # that the guard let the request through.
        self.assertEqual(resp.status_code, 400)

    def test_authenticated_delete_reaches_the_route(self):
        self._login()
        resp = self.client.delete("/backing")
        # No such route: 405 means the guard did not block it.
        self.assertIn(resp.status_code, (400, 404, 405))

    # -- GET stays open --------------------------------------------------

    def test_unauthenticated_get_is_allowed(self):
        resp = self.client.get("/api/state")
        self.assertEqual(resp.status_code, 200)

    def test_unauthenticated_get_of_index_is_allowed(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    # -- no password configured: app stays open --------------------------

    def test_no_password_configured_leaves_app_open(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GX_PASSWORD", None)
            resp = self.client.post("/backing")
            self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
