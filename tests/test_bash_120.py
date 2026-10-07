"""Regression tests for issue #120: preset-operation transaction protocol.

These drive the real socket handlers in app.py against the fake engine in
tests/fakes/engine.py, so the whole path is exercised: handler ->
_preset_action -> _preset_transaction -> gx_rpc -> engine -> toast/op_done.

The fake engine is started in-process on an ephemeral port and app.py's
module-level rpc object is pointed at it. No network beyond loopback, no
filesystem writes outside a temp dir.
"""

import importlib
import os
import socket
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAKES = os.path.join(HERE, "fakes")
STUBS = os.path.join(HERE, "stubs")

for path in (ROOT, FAKES, STUBS):
    if path not in sys.path:
        sys.path.insert(0, path)


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _EngineThread:
    """Runs tests/fakes/engine.py's FakeEngine on a background thread."""

    def __init__(self, port):
        self.port = port
        self.engine = None
        self.thread = None

    def start(self):
        import engine as fake_engine

        os.environ["GX_PORT"] = str(self.port)
        self.engine = fake_engine.FakeEngine()
        self.engine.port = self.port
        self.thread = threading.Thread(target=self.engine.start, daemon=True)
        self.thread.start()
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=0.5)
                s.close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("fake engine did not come up")

    def stop(self):
        if self.engine is not None:
            self.engine.running = False
        try:
            s = socket.create_connection(("127.0.0.1", self.port), timeout=0.5)
            s.close()
        except OSError:
            pass


class PresetTransactionTest(unittest.TestCase):
    def setUp(self):
        self.port = _free_port()
        self.engine_thread = _EngineThread(self.port)
        self.engine_thread.start()
        self.addCleanup(self.engine_thread.stop)

        # Import app fresh so its module-level rpc/state are ours alone.
        for name in ("app", "gx_rpc", "flask_socketio"):
            sys.modules.pop(name, None)
        self.app = importlib.import_module("app")

        # Point the app's rpc object at the fake engine.
        self.app.rpc.host = "127.0.0.1"
        self.app.rpc.port = self.port
        self.app.rpc.stop()
        self.app.rpc = self.app.gx_rpc.GuitarixRPC(
            host="127.0.0.1", port=self.port,
            on_params=self.app.on_params,
            on_event=self.app.on_event,
            on_status=self.app.on_status,
            on_ready=self.app.on_ready,
        )
        self.app.rpc.start()
        self.addCleanup(self.app.rpc.stop)

        deadline = time.time() + 5.0
        while time.time() < deadline and not self.app.rpc.connected:
            time.sleep(0.05)
        self.assertTrue(self.app.rpc.connected, "rpc did not connect")

        self.toasts = []
        self.dones = []
        self.app.toast = lambda text, kind="info": self.toasts.append(
            {"text": text, "kind": kind})
        self.app.done = lambda op, ok: self.dones.append({"op": op, "ok": ok})

        # Run background tasks inline so assertions are deterministic.
        self.app.socketio.start_background_task = (
            lambda target, *a, **kw: target(*a, **kw))

    # ---------------------------------------------------------------- helpers

    def _wait(self, predicate, timeout=8.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def _banks(self):
        return {b["name"]: list(b["presets"]) for b in self.app.rpc.banks()}

    def _error_toasts(self):
        return [t for t in self.toasts if t["kind"] == "error"]

    # ---------------------------------------------------------------- cases

    def test_rename_ignored_by_engine_is_ambiguous(self):
        """Engine ignores the notify: bank list unchanged -> ambiguous error."""
        # Make the engine silently drop rename_preset.
        original = self.engine_thread.engine._process_request

        def dropping(client_socket, line):
            import json
            try:
                req = json.loads(line)
            except ValueError:
                return original(client_socket, line)
            if req.get("method") == "rename_preset":
                return None
            return original(client_socket, line)

        self.engine_thread.engine._process_request = dropping

        before = self._banks()
        self.app.client_preset_rename(
            {"bank": "Warm", "old": "Crunch", "new": "Crunchy", "op": "op-1"})

        self.assertTrue(self._wait(lambda: self.dones), "no op_done emitted")
        self.assertFalse(self.dones[-1]["ok"])
        self.assertEqual(self.dones[-1]["op"], "op-1")

        errors = self._error_toasts()
        self.assertTrue(errors, "expected an error toast")
        self.assertIn("ambiguous", errors[-1]["text"].lower())

        # Nothing changed on the engine.
        self.assertEqual(self._banks(), before)

    def test_rename_applied_by_engine_succeeds(self):
        """Engine applies the rename: ok True, no ambiguous toast."""
        self.app.client_preset_rename(
            {"bank": "Warm", "old": "Crunch", "new": "Crunchy", "op": "op-2"})

        self.assertTrue(self._wait(lambda: self.dones), "no op_done emitted")
        self.assertTrue(self.dones[-1]["ok"])
        self.assertEqual(self.dones[-1]["op"], "op-2")

        for t in self.toasts:
            self.assertNotIn("ambiguous", t["text"].lower())

        self.assertIn("Crunchy", self._banks().get("Warm", []))

    def test_save_as_ignored_by_engine_is_ambiguous(self):
        """Same protocol via client_preset_save_as."""
        original = self.engine_thread.engine._process_request

        def dropping(client_socket, line):
            import json
            try:
                req = json.loads(line)
            except ValueError:
                return original(client_socket, line)
            if req.get("method") == "save_preset":
                return None
            return original(client_socket, line)

        self.engine_thread.engine._process_request = dropping

        before = self._banks()
        self.app.client_preset_save_as(
            {"bank": "Warm", "name": "NewOne", "op": "op-3"})

        self.assertTrue(self._wait(lambda: self.dones), "no op_done emitted")
        self.assertFalse(self.dones[-1]["ok"])
        errors = self._error_toasts()
        self.assertTrue(errors, "expected an error toast")
        self.assertIn("ambiguous", errors[-1]["text"].lower())
        self.assertEqual(self._banks(), before)

    def test_transaction_helper_reports_before_after_and_ok(self):
        """_preset_transaction returns the documented dict."""
        before = self._banks()
        result = self.app._preset_transaction(
            lambda: self.app.rpc.preset_rename("Warm", "Crunch", "Crunchy"),
            lambda m: "Crunchy" in m.get("Warm", []),
            settle=3.0)
        self.assertEqual(result["before"], before)
        self.assertTrue(result["ok"])
        self.assertFalse(result["ambiguous"])
        self.assertIn("Crunchy", result["after"].get("Warm", []))

    def test_transaction_helper_flags_ambiguous_when_ignored(self):
        """No change at all -> ambiguous True."""
        before = self._banks()
        result = self.app._preset_transaction(
            lambda: None,
            lambda m: "NeverThere" in m.get("Warm", []),
            settle=1.0)
        self.assertEqual(result["before"], before)
        self.assertEqual(result["after"], before)
        self.assertFalse(result["ok"])
        self.assertTrue(result["ambiguous"])

    def test_transaction_helper_not_ambiguous_when_state_moved(self):
        """State changed but verify still failed -> not ambiguous."""
        result = self.app._preset_transaction(
            lambda: self.app.rpc.preset_rename("Warm", "Crunch", "Crunchy"),
            lambda m: "SomethingElse" in m.get("Warm", []),
            settle=1.0)
        self.assertFalse(result["ok"])
        self.assertFalse(result["ambiguous"])
        self.assertNotEqual(result["before"], result["after"])


if __name__ == "__main__":
    unittest.main()
