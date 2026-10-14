import unittest

import app as amp


class SessionCookieTest(unittest.TestCase):
    def test_get_sets_strict_httponly_session_cookie(self):
        with amp.app.test_client() as client:
            with client.session_transaction() as session:
                session.permanent = True

            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("HttpOnly", cookie)


if __name__ == "__main__":
    unittest.main()
