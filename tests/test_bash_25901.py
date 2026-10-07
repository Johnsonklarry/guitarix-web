import importlib
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import probe_rpc


UNKNOWN = {"error": {"code": -32601, "message": "Method not found"}}
BAD_ARGS = {"error": {"code": -32602, "message": "Invalid params"}}


class FakeRPC:
    """Stand-in for GuitarixRPC: no sockets, canned replies keyed by method name."""

    def __init__(self, responses, on_ready=None):
        self.host = "127.0.0.1"
        self.port = 7000
        self.responses = responses
        self.on_ready = on_ready
        self.calls = []
        self.stopped = False

    def start(self):
        if self.on_ready is not None:
            self.on_ready()

    def stop(self):
        self.stopped = True

    def call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return self.responses.get(name, UNKNOWN)


class ClassifyTests(unittest.TestCase):
    def test_unknown_method_code(self):
        self.assertEqual(probe_rpc.classify({"error": {"code": -32601}}), "unknown")

    def test_argument_error_is_real(self):
        self.assertEqual(probe_rpc.classify({"error": {"code": -32602}}), "real")

    def test_result_reply_is_real(self):
        self.assertEqual(probe_rpc.classify({"result": None}), "real")


class ImportSafetyTests(unittest.TestCase):
    def test_import_does_not_connect_or_exit(self):
        saved = sys.modules.get("probe_rpc")
        self.addCleanup(lambda: sys.modules.__setitem__("probe_rpc", saved))
        sys.modules.pop("probe_rpc", None)
        # Patch the client class itself, not just socket.socket: a module-level
        # GuitarixRPC(...) would otherwise connect from a background thread,
        # where an AssertionError raised by the patched socket never reaches us.
        with mock.patch.object(probe_rpc.gx_rpc, "GuitarixRPC",
                               side_effect=AssertionError("RPC client built at import")) as rpc_cls, \
                mock.patch("sys.exit") as exit_mock, \
                mock.patch("socket.socket", side_effect=AssertionError("network used at import")):
            module = importlib.import_module("probe_rpc")
        rpc_cls.assert_not_called()
        exit_mock.assert_not_called()
        self.assertTrue(callable(getattr(module, "main", None)))
        self.assertTrue(callable(getattr(module, "classify", None)))


class MainTests(unittest.TestCase):
    def _run_main(self, responses, candidates):
        holder = {}

        def factory(on_ready=None, **kwargs):
            holder["rpc"] = FakeRPC(responses, on_ready=on_ready)
            return holder["rpc"]

        buf = io.StringIO()
        with mock.patch.object(probe_rpc, "GuitarixRPC", side_effect=factory), \
                mock.patch.object(probe_rpc.gx_rpc, "CANDIDATES", candidates):
            with redirect_stdout(buf):
                probe_rpc.main()
        return buf.getvalue(), holder["rpc"]

    def test_main_prints_classification_per_method(self):
        out, rpc = self._run_main(
            {"save_as": UNKNOWN, "save_preset": BAD_ARGS},
            {"save_as": ["save_as", "save_preset"]},
        )
        lines = out.splitlines()
        self.assertTrue(any("save_as" in l and "unknown" in l for l in lines), out)
        self.assertTrue(any("save_preset" in l and "real" in l for l in lines), out)
        self.assertIn('"save_preset"', out)
        self.assertEqual([c[0] for c in rpc.calls], ["save_as", "save_preset"])
        self.assertTrue(rpc.stopped)

    def test_main_reports_nothing_found(self):
        out, _ = self._run_main({}, {"delete": ["delete_preset"]})
        self.assertIn("NOTHING FOUND", out)
        self.assertIn("No candidate matched for: delete", out)


if __name__ == "__main__":
    unittest.main()
