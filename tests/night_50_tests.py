"""Standalone regression tests for playback command acknowledgements."""

import ast
from pathlib import Path
import unittest
from unittest.mock import Mock


def load_handlers():
    source = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(source.read_text())
    names = {"_reamp_call", "client_backing_volume"}
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    if len(functions) != len(names):
        raise AssertionError("playback handlers not found")
    for function in functions:
        function.decorator_list = []

    class BackingError(Exception):
        pass

    class ReampError(Exception):
        pass

    namespace = {
        "BackingError": BackingError,
        "ReampError": ReampError,
        "socketio": Mock(),
        "rec_payload": Mock(return_value={"authoritative": True}),
        "done": Mock(),
        "toast": Mock(),
        "backing": Mock(),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"),
         namespace)
    return namespace


class PlaybackAcknowledgementTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_handlers()
        self.player = self.ns["backing"].player

    def assert_completion(self, ok):
        self.ns["done"].assert_called_once_with("operation-1", ok)
        self.ns["socketio"].emit.assert_called_once_with(
            "rec", {"authoritative": True})

    def test_rejected_volume_reports_failure_and_restores_state(self):
        self.player.set_volume.return_value = False
        self.ns["client_backing_volume"]({"volume": 42, "op": "operation-1"})
        self.player.set_volume.assert_called_once_with(42)
        self.assert_completion(False)

    def test_disconnected_player_reports_failure(self):
        self.player.set_volume.side_effect = OSError("disconnected")
        self.ns["client_backing_volume"]({"volume": 42, "op": "operation-1"})
        self.assert_completion(False)

    def test_timeout_reports_failure(self):
        self.player.set_volume.side_effect = TimeoutError("timed out")
        self.ns["client_backing_volume"]({"volume": 42, "op": "operation-1"})
        self.assert_completion(False)

    def test_invalid_volumes_do_not_reach_player(self):
        for value in ("loud", -1, 101, True, 2.5, None):
            with self.subTest(value=value):
                self.setUp()
                self.ns["client_backing_volume"](
                    {"volume": value, "op": "operation-1"})
                self.player.set_volume.assert_not_called()
                self.assert_completion(False)

    def test_accepted_volume_completes_once(self):
        self.player.set_volume.return_value = True
        self.ns["client_backing_volume"]({"volume": "42", "op": "operation-1"})
        self.player.set_volume.assert_called_once_with(42)
        self.assert_completion(True)


if __name__ == "__main__":
    unittest.main()
