"""Issue #48: shared parameter validation between live controls and imports."""

import unittest
from unittest import mock

import app as app_mod
import presets_io


def _num(minimum=0.0, maximum=1.0, step=0.01):
    return {"min": minimum, "max": maximum, "step": step, "type": "float",
            "non_preset": False}


def _switch():
    return {"min": 0, "max": 1, "step": 1, "type": "bool", "non_preset": False}


def _choice():
    return {"min": 0, "max": 1, "step": 1, "type": "float", "non_preset": False,
            "options": [
                {"value": 0, "key": "clean", "label": "Clean"},
                {"value": 1, "key": "dirty", "label": "Dirty"},
            ]}


class CoerceTests(unittest.TestCase):
    def test_switch_forms(self):
        p = _switch()
        self.assertEqual(presets_io.coerce(p, True), (1, None))
        self.assertEqual(presets_io.coerce(p, "on"), (1, None))
        self.assertEqual(presets_io.coerce(p, "off"), (0, None))
        with self.assertRaises(presets_io.Rejected):
            presets_io.coerce(p, 2)

    def test_choice_accepts_label_index_and_key(self):
        p = _choice()
        for raw in ("Clean", 0, "clean"):
            value, clamp = presets_io.coerce(p, raw)
            self.assertEqual(value["label"], "Clean")
            self.assertIsNone(clamp)
        value, _ = presets_io.coerce(p, "Dirty")
        self.assertEqual(value["value"], 1)
        with self.assertRaises(presets_io.Rejected):
            presets_io.coerce(p, "nope")

    def test_non_finite_numbers_are_rejected_everywhere(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(presets_io.Rejected):
                presets_io.coerce(_num(), bad)
            with self.assertRaises(presets_io.Rejected):
                presets_io.coerce(_choice(), bad)

    def test_import_clamps_but_live_rejects(self):
        p = _num(0.0, 1.0)
        value, clamp = presets_io.coerce(p, 5)
        self.assertEqual(value, 1.0)
        self.assertEqual(clamp, (5, 1.0))
        with self.assertRaises(presets_io.Rejected):
            presets_io.coerce(p, 5, live=True)
        # an in-range value is untouched by live mode
        self.assertEqual(presets_io.coerce(p, 0.5, live=True), (0.5, None))

    def test_integer_step_rounds_without_quantizing(self):
        p = _num(0.0, 10.0, step=1)
        self.assertEqual(presets_io.coerce(p, 3.7)[0], 4)
        self.assertEqual(presets_io.coerce(p, 3.2)[0], 3)
        self.assertEqual(presets_io.coerce(p, "7")[0], 7)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.params = {"amp.on_off": _switch(), "amp.gain": _num(0.0, 1.0)}
        self.current = {"amp.on_off": 0, "amp.gain": 0.5}

    def test_unknown_id_is_rejected(self):
        preset = {"name": "x", "params": {"nope.gain": 0.2, "amp.gain": 0.25}}
        changes, report = presets_io.plan(preset, self.params, self.current)
        self.assertIn("amp.gain", changes)
        self.assertTrue(any(r["id"] == "nope.gain" for r in report["rejected"]))

    def test_out_of_range_is_clamped_and_reported(self):
        preset = {"name": "x", "params": {"amp.gain": 5}}
        changes, report = presets_io.plan(preset, self.params, self.current)
        self.assertEqual(changes["amp.gain"], 1.0)
        self.assertTrue(any(c["id"] == "amp.gain" for c in report["clamped"]))

    def test_non_finite_is_rejected(self):
        preset = {"name": "x", "params": {"amp.gain": float("nan")}}
        _, report = presets_io.plan(preset, self.params, self.current)
        self.assertTrue(any(r["id"] == "amp.gain" for r in report["rejected"]))


class SetParamEntryTests(unittest.TestCase):
    def setUp(self):
        self._saved = (app_mod.state.parameters, app_mod.state.values)
        self.addCleanup(self._restore_state)

        app_mod.state.parameters = {
            "amp.gain": _num(0.0, 1.0),
            "amp.fuzz": _choice(),
            "amp.on_off": _switch(),
            "amp.hidden": dict(_num(0.0, 1.0), non_preset=True),
        }
        app_mod.state.values = {"amp.gain": 0.5, "amp.fuzz": "dirty"}

        self.engine = {"amp.gain": 0.42, "amp.fuzz": "dirty", "amp.on_off": 0}

        self.rpc = mock.Mock()
        self.rpc.get.side_effect = lambda ids, **kw: {
            pid: self.engine.get(pid) for pid in ids}
        self._patch("rpc", self.rpc)

        self.emitted = []
        self._patch("socketio.emit", mock.Mock(side_effect=self._record_emit))

        self.queued = []
        self._patch("queue_params", lambda changes: self.queued.append(dict(changes)))

        self.dirty = []
        self._patch("set_dirty", lambda value: self.dirty.append(value))

        self.toasted = []
        self._patch("toast", lambda text, kind="info": self.toasted.append((text, kind)))

        self._patch("state.snapshot", mock.Mock(return_value={"snapshot": True}))

    def _restore_state(self):
        app_mod.state.parameters, app_mod.state.values = self._saved

    def _patch(self, target, value):
        obj, _, attr = target.rpartition(".")
        obj = getattr(app_mod, obj) if obj else app_mod
        patcher = mock.patch.object(obj, attr, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _record_emit(self, event, data=None, *args, **kwargs):
        self.emitted.append((event, data, kwargs))
        return None

    def test_malformed_message_gets_snapshot(self):
        app_mod.client_set_param(None)
        self.rpc.set.assert_not_called()
        self.assertEqual(self.dirty, [])
        self.assertEqual(self.emitted[0][0], "snapshot")

    def test_missing_id_gets_snapshot(self):
        app_mod.client_set_param({})
        self.rpc.set.assert_not_called()
        self.assertEqual(self.emitted[0][0], "snapshot")

    def test_unknown_id_gets_snapshot(self):
        app_mod.client_set_param({"id": "nope.gain", "value": 0.5})
        self.rpc.set.assert_not_called()
        self.assertEqual(self.dirty, [])
        self.assertEqual(self.emitted[0][0], "snapshot")

    def test_non_preset_id_is_rejected_and_resynced(self):
        app_mod.client_set_param({"id": "amp.hidden", "value": 0.5})
        self.rpc.set.assert_not_called()
        self.assertEqual(self.dirty, [])
        self.assertEqual(self.emitted[0][0], "params")

    def test_out_of_range_is_rejected_and_resynced(self):
        app_mod.client_set_param({"id": "amp.gain", "value": 5})
        self.rpc.set.assert_not_called()
        self.assertEqual(self.dirty, [])
        self.assertEqual(self.emitted[0][0], "params")

    def test_bad_switch_is_rejected(self):
        app_mod.client_set_param({"id": "amp.on_off", "value": 2})
        self.rpc.set.assert_not_called()
        self.assertEqual(self.dirty, [])
        self.assertEqual(self.emitted[0][0], "params")

    def test_choice_label_written_in_engine_representation(self):
        app_mod.client_set_param({"id": "amp.fuzz", "value": "Clean"})
        self.rpc.set.assert_called_once_with(
            {"amp.fuzz": "clean"}, lane=app_mod.gx_rpc.HIGH)

    def test_choice_index_written_in_engine_representation(self):
        app_mod.state.values = {"amp.fuzz": 1}
        self.engine["amp.fuzz"] = 1
        app_mod.client_set_param({"id": "amp.fuzz", "value": 0})
        self.rpc.set.assert_called_once_with(
            {"amp.fuzz": 0}, lane=app_mod.gx_rpc.HIGH)

    def test_success_queues_engine_readback_not_submitted(self):
        self.engine["amp.gain"] = 0.33
        app_mod.client_set_param({"id": "amp.gain", "value": 0.9})
        self.rpc.set.assert_called_once_with(
            {"amp.gain": 0.9}, lane=app_mod.gx_rpc.HIGH)
        self.assertEqual(self.queued, [{"amp.gain": 0.33}])
        self.assertEqual(self.dirty, [True])

    def test_confirmation_failure_does_not_mark_dirty(self):
        self.rpc.get.side_effect = OSError("no reply")
        app_mod.client_set_param({"id": "amp.gain", "value": 0.5})
        self.rpc.set.assert_called_once()
        self.assertEqual(self.dirty, [])
        self.assertTrue(self.toasted)
        self.assertEqual(self.emitted[0][0], "snapshot")


if __name__ == "__main__":
    unittest.main()
