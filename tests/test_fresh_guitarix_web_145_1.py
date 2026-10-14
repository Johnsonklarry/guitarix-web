import os
import unittest
from unittest.mock import patch

import app


class SocketIOAuthGuardTest(unittest.TestCase):
    def setUp(self):
        self.password = patch.dict(os.environ, {"GX_PASSWORD": "test-password"})
        self.password.start()
        self.broadcast = patch.object(app, "BROADCAST", False)
        self.broadcast.start()
        self.demo = patch.object(app, "DEMO_ONLY", False)
        self.demo.start()
        self.rec_payload = patch.object(app, "rec_payload", return_value={})
        self.rec_payload.start()
        self.original_include = app.backing.include_in_takes
        app.backing.include_in_takes = False

    def tearDown(self):
        app.backing.include_in_takes = self.original_include
        self.rec_payload.stop()
        self.demo.stop()
        self.broadcast.stop()
        self.password.stop()

    def test_unauthenticated_event_is_rejected_without_state_change(self):
        flask_client = app.app.test_client()
        client = app.socketio.test_client(app.app, flask_test_client=flask_client)
        self.assertTrue(client.is_connected())
        client.get_received()

        before = app.backing.include_in_takes
        client.emit("backing_include", {"include": True})

        self.assertEqual(app.backing.include_in_takes, before)
        self.assertEqual(client.get_received(), [])
        client.disconnect()

    def test_authenticated_event_runs_normally(self):
        flask_client = app.app.test_client()
        with flask_client.session_transaction() as session:
            session["authenticated"] = True
        client = app.socketio.test_client(app.app, flask_test_client=flask_client)
        self.assertTrue(client.is_connected())
        client.get_received()

        client.emit("backing_include", {"include": True})

        self.assertTrue(app.backing.include_in_takes)
        received = client.get_received()
        self.assertTrue(any(message["name"] == "rec" for message in received))
        client.disconnect()


if __name__ == "__main__":
    unittest.main()
