"""Regression tests for issue #130: local preset-request compiler.

Covers the diff-only brief produced by presets_io.compile_brief and its
wiring into presets_io.plan, plus the ticket's RPC-side assertions run
against a fake GuitarixRPC that records outgoing JSON-RPC requests.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import presets_io  # noqa: E402


def _param(name, lo, hi, default=None, step=None, unit=None):
    p = {"name": name, "min": lo, "max": hi, "type": "float"}
    if default is not None:
        p["default"] = default
    if step is not None:
        p["step"] = step
    if unit is not None:
        p["unit"] = unit
    return p


PARAMETERS = {
    "amp.on_off": {"name": "Amp", "min": 0, "max": 1, "step": 1, "type": "bool"},
    "amp.gain": _param("Gain", 0.0, 1.0, default=0.5, step=0.01),
    "amp.master": _param("Master", 0.0, 1.0, default=0.5, step=0.01),
    "amp.bass": _param("Bass", 0.0, 1.0, default=0.5, step=0.01),
    "amp.treble": _param("Treble", 0.0, 1.0, default=0.5, step=0.01),
    "freeverb.on_off": {"name": "Reverb", "min": 0, "max": 1, "step": 1, "type": "bool"},
    "freeverb.RoomSize": _param("Room size", 0.0, 1.0, default=0.5, step=0.01),
    "freeverb.Wet": _param("Wet", 0.0, 1.0, default=0.5, step=0.01),
}

BANKS = {
    "Claude": {
        "Sultans Clean": {
            "params": {
                "amp.on_off": 1,
                "amp.gain": 0.4,
                "amp.master": 0.5,
                "amp.bass": 0.5,
                "amp.treble": 0.5,
                "freeverb.on_off": 1,
                "freeverb.RoomSize": 0.35,
                "freeverb.Wet": 0.5,
            }
        }
    }
}


class CompileBriefTests(unittest.TestCase):
    def test_only_differing_parameters_appear(self):
        brief = presets_io.compile_brief(
            "Claude/Sultans Clean", {"amp.gain": 0.8}, parameters=PARAMETERS, banks=BANKS)
        self.assertIsNone(brief["error"])
        self.assertEqual(list(brief["params"]), ["amp.gain"])
        self.assertEqual(brief["params"]["amp.gain"]["value"], 0.8)
        self.assertEqual(brief["params"]["amp.gain"]["min"], 0.0)
        self.assertEqual(brief["params"]["amp.gain"]["max"], 1.0)
        self.assertEqual(brief["params"]["amp.gain"]["unit"], "amp.")
        self.assertEqual(brief["base"], {"bank": "Claude", "preset": "Sultans Clean"})

    def test_unchanged_parameters_are_excluded(self):
        brief = presets_io.compile_brief(
            "Claude/Sultans Clean",
            {"amp.gain": 0.4, "freeverb.RoomSize": 0.35},
            parameters=PARAMETERS, banks=BANKS)
        self.assertEqual(brief["params"], {})

    def test_brief_is_at_least_half_smaller_than_full_preset(self):
        full = json.dumps(BANKS["Claude"]["Sultans Clean"]["params"], sort_keys=True)
        brief = presets_io.compile_brief(
            "Claude/Sultans Clean", {"amp.gain": 0.8}, parameters=PARAMETERS, banks=BANKS)
        brief_json = json.dumps(brief["params"], sort_keys=True)
        self.assertLessEqual(len(brief_json), len(full) / 2)

    def test_out_of_range_is_a_structured_error(self):
        brief = presets_io.compile_brief(
            "Claude/Sultans Clean", {"amp.gain": 5.0}, parameters=PARAMETERS, banks=BANKS)
        self.assertIsNotNone(brief["error"])
        self.assertEqual(brief["error"]["kind"], "invalid-request")
        self.assertEqual(brief["clamped"][0]["id"], "amp.gain")
        self.assertEqual(brief["clamped"][0]["used"], 1.0)
        self.assertEqual(brief["params"]["amp.gain"]["value"], 1.0)

    def test_unknown_id_is_a_structured_error(self):
        brief = presets_io.compile_brief(
            "Claude/Sultans Clean", {"amp.gian": 0.8}, parameters=PARAMETERS, banks=BANKS)
        self.assertIsNotNone(brief["error"])
        self.assertEqual(brief["rejected"][0]["id"], "amp.gian")
        self.assertEqual(brief["rejected"][0]["suggestion"], "amp.gain")
        self.assertEqual(brief["params"], {})

    def test_no_base_means_everything_differs(self):
        brief = presets_io.compile_brief(
            None, {"amp.gain": 0.8}, parameters=PARAMETERS, banks=BANKS)
        self.assertIsNone(brief["base"])
        self.assertEqual(list(brief["params"]), ["amp.gain"])

    def test_brief_is_json_serialisable(self):
        brief = presets_io.compile_brief(
            "Claude/Sultans Clean", {"amp.gain": 0.8}, parameters=PARAMETERS, banks=BANKS)
        json.dumps(brief)


class PlanReturnsBriefTests(unittest.TestCase):
    def _preset(self, params, base=None):
        p = {"name": "Test", "notes": "", "params": params}
        if base:
            p["base"] = base
            p["banks"] = BANKS
        return p

    def test_plan_returns_brief_for_one_parameter(self):
        preset = self._preset({"amp.gain": 0.8}, base="Claude/Sultans Clean")
        current = {pid: p.get("default", 0) for pid, p in PARAMETERS.items()}
        changes, report = presets_io.plan(preset, PARAMETERS, current)
        self.assertIn("brief", report)
        self.assertEqual(list(report["brief"]["params"]), ["amp.gain"])
        self.assertEqual(changes["amp.gain"], 0.8)

    def test_plan_brief_is_at_least_half_smaller_than_full_preset(self):
        preset = self._preset({"amp.gain": 0.8}, base="Claude/Sultans Clean")
        current = {pid: p.get("default", 0) for pid, p in PARAMETERS.items()}
        _, report = presets_io.plan(preset, PARAMETERS, current)
        full = json.dumps(BANKS["Claude"]["Sultans Clean"]["params"], sort_keys=True)
        brief_json = json.dumps(report["brief"]["params"], sort_keys=True)
        self.assertLessEqual(len(brief_json), len(full) / 2)

    def test_plan_brief_reports_out_of_range_without_provider_call(self):
        preset = self._preset({"amp.gain": 5.0}, base="Claude/Sultans Clean")
        current = {pid: p.get("default", 0) for pid, p in PARAMETERS.items()}
        _, report = presets_io.plan(preset, PARAMETERS, current)
        self.assertIsNotNone(report["brief"]["error"])
        self.assertEqual(report["brief"]["clamped"][0]["id"], "amp.gain")

    def test_plan_without_base_still_returns_brief(self):
        preset = self._preset({"amp.gain": 0.8})
        current = {pid: p.get("default", 0) for pid, p in PARAMETERS.items()}
        _, report = presets_io.plan(preset, PARAMETERS, current)
        self.assertIn("brief", report)
        self.assertIsNone(report["brief"]["base"])


class FakeGuitarixRPC:
    """Minimal stand-in for the real RPC client, recording outgoing requests."""

    def __init__(self):
        self.next_id = 1
        self.pending = {}
        self.log = []
        self.responses = {}

    def call(self, method, params=None):
        rid = self.next_id
        self.next_id += 1
        request = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or []}
        self.log.append(request)
        self.pending[rid] = request
        return self.responses.get(rid)

    def dispatch(self, reply):
        rid = reply.get("id")
        if rid not in self.pending:
            self.log.append({"ignored": reply})
            return None
        del self.pending[rid]
        return reply.get("result")


class RpcTicketTests(unittest.TestCase):
    def test_ids_are_monotonic_from_one(self):
        rpc = FakeGuitarixRPC()
        for _ in range(5):
            rpc.call("get", ["amp.gain"])
        ids = [r["id"] for r in rpc.log]
        self.assertEqual(ids, [1, 2, 3, 4, 5])

    def test_second_call_resolves_before_first(self):
        rpc = FakeGuitarixRPC()
        first = rpc.call("get", ["amp.gain"])
        second = rpc.call("get", ["amp.master"])
        self.assertIsNone(first)
        self.assertIsNone(second)
        rpc.responses[2] = 0.7
        self.assertEqual(rpc.dispatch({"id": 2, "result": 0.7}), 0.7)
        self.assertIn(1, rpc.pending)
        self.assertNotIn(2, rpc.pending)

    def test_unknown_reply_is_ignored_and_logged(self):
        rpc = FakeGuitarixRPC()
        rpc.call("get", ["amp.gain"])
        before = len(rpc.pending)
        result = rpc.dispatch({"id": 999, "result": 0.1})
        self.assertIsNone(result)
        self.assertEqual(len(rpc.pending), before)
        self.assertIn({"ignored": {"id": 999, "result": 0.1}}, rpc.log)

    def test_already_resolved_reply_is_ignored(self):
        rpc = FakeGuitarixRPC()
        rpc.call("get", ["amp.gain"])
        rpc.dispatch({"id": 1, "result": 0.5})
        result = rpc.dispatch({"id": 1, "result": 0.9})
        self.assertIsNone(result)

    def test_probe_classifies_valid_and_unknown_methods(self):
        rpc = FakeGuitarixRPC()
        rpc.responses[1] = {"error": {"code": -32601, "message": "method not found"}}
        rpc.responses[2] = {"error": {"code": -32602, "message": "invalid params"}}
        rpc.call("get")
        rpc.call("set")
        known = []
        for rid, request in list(rpc.pending.items()):
            reply = rpc.responses.get(rid)
            if reply and reply.get("error", {}).get("code") == -32601:
                continue
            known.append(request["method"])
        self.assertEqual(known, ["set"])

    def test_local_compiler_excludes_unmodified_parameters(self):
        brief = presets_io.compile_brief(
            "Claude/Sultans Clean", {"amp.gain": 0.8}, parameters=PARAMETERS, banks=BANKS)
        self.assertNotIn("amp.master", brief["params"])
        self.assertNotIn("freeverb.RoomSize", brief["params"])
        self.assertEqual(list(brief["params"]), ["amp.gain"])


class TempDirSafetyTests(unittest.TestCase):
    def test_no_filesystem_writes_outside_tempdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "brief.json")
            brief = presets_io.compile_brief(
                "Claude/Sultans Clean", {"amp.gain": 0.8}, parameters=PARAMETERS, banks=BANKS)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(brief, fh)
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
