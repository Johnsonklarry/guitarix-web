"""Prep test for the widget-kind backlog item (atom_guitarix_web_2850703_1).

Fails on today's controls.py: there is no controls.widget_kind and emitted
controls carry no `kind` field. Passes once widget_kind exists and _group
stamps `kind` onto every member it emits.
"""

import unittest

import controls


KINDS = {"slider", "choice", "switch"}


def param(pid, name=None, kind="float", low=0.0, high=1.0, step=0.01, options=None):
    """Shape-matching stand-in for one entry of GuitarixRPC.parameter_list()."""
    return {
        "id": pid,
        "name": name if name is not None else pid.split(".")[-1],
        "type": kind,
        "min": low,
        "max": high,
        "step": step,
        "options": options,
    }


def fake_eq_params():
    """Enough of a rack for every prefix in controls.EQ_GROUPS to render."""
    return {
        # tone stack
        "tone.on_off": param("tone.on_off", "on_off", "bool", 0, 1, 1),
        "tone.bass": param("tone.bass"),
        "tone.middle": param("tone.middle"),
        "tone.treble": param("tone.treble"),
        # graphic EQ: two bands, three parameters each
        "eqs.on_off": param("eqs.on_off", "on_off", "bool", 0, 1, 1),
        "eqs.31_25": param("eqs.31_25"),
        "eqs.Qs31_25": param("eqs.Qs31_25"),
        "eqs.Freq31_25": param("eqs.Freq31_25"),
        "eqs.1k": param("eqs.1k"),
        "eqs.Qs1k": param("eqs.Qs1k"),
        "eqs.Freq1k": param("eqs.Freq1k"),
        # cut filter, with the per-unit plumbing that is hidden
        "low_highpass.on_off": param("low_highpass.on_off", "on_off", "bool", 0, 1, 1),
        "low_highpass.low": param("low_highpass.low"),
        "low_highpass.high": param("low_highpass.high"),
        "low_highpass.s_h": param("low_highpass.s_h", "s_h", "bool", 0, 1, 1),
        # amp, including a picker with options
        "amp.on_off": param("amp.on_off", "on_off", "bool", 0, 1, 1),
        "amp.fuzz": param("amp.fuzz"),
        "amp.bass": param("amp.bass"),
        "amp.middle": param("amp.middle"),
        "amp.treble": param("amp.treble"),
        "amp.balance": param("amp.balance"),
        "amp.wet_dry": param("amp.wet_dry"),
        "amp.out_amp": param("amp.out_amp"),
        "amp.out_master": param("amp.out_master"),
        "amp.select": param(
            "amp.select", "select", "float", 0, 3, 1,
            options=["Triple Giant", "Bassman", "JCM-800"],
        ),
    }


class WidgetKindTests(unittest.TestCase):
    def test_non_empty_options_is_a_choice(self):
        info = param("amp.select", "select", options=["Triple Giant", "Bassman"])
        self.assertEqual(controls.widget_kind("amp.select", info, []), "choice")

    def test_zero_one_step_one_is_a_switch(self):
        info = param("amp.on_off", "on_off", "float", 0, 1, 1)
        self.assertEqual(controls.widget_kind("amp.on_off", info, []), "switch")

    def test_bool_type_is_a_switch(self):
        info = param("amp.on_off", "on_off", "bool", 0, 1, 1)
        self.assertEqual(controls.widget_kind("amp.on_off", info, []), "switch")

    def test_continuous_is_a_slider(self):
        info = param("amp.fuzz", "fuzz", "float", 0.0, 1.0, 0.01)
        self.assertEqual(controls.widget_kind("amp.fuzz", info, []), "slider")

    def test_empty_options_do_not_make_a_choice(self):
        info = param("amp.on_off", "on_off", "bool", 0, 1, 1, options=[])
        self.assertEqual(controls.widget_kind("amp.on_off", info, []), "switch")

    def test_widgets_entry_overrides_the_guess(self):
        info = param("amp.fuzz", "fuzz", "float", 0.0, 1.0, 0.01)
        widgets = [{"match": r"amp[.]fuzz$", "widget": "switch"}]
        self.assertEqual(controls.widget_kind("amp.fuzz", info, widgets), "switch")

    def test_widgets_entry_overrides_an_options_choice(self):
        info = param("amp.select", "select", options=["Triple Giant", "Bassman"])
        widgets = [{"match": r"select", "widget": "slider"}]
        self.assertEqual(controls.widget_kind("amp.select", info, widgets), "slider")

    def test_widgets_entry_that_does_not_match_falls_through(self):
        info = param("amp.select", "select", options=["Triple Giant", "Bassman"])
        widgets = [{"match": r"echo[.]", "widget": "slider"}]
        self.assertEqual(controls.widget_kind("amp.select", info, widgets), "choice")


class BuildGroupsKindTests(unittest.TestCase):
    def test_every_control_from_eq_groups_carries_a_kind(self):
        groups = controls.build_groups(controls.EQ_GROUPS, fake_eq_params())
        self.assertTrue(groups)
        seen = 0
        for group in groups:
            for control in group["controls"]:
                self.assertIn("kind", control)
                self.assertIn(control["kind"], KINDS)
                seen += 1
        self.assertGreater(seen, 0)

    def test_group_widgets_override_reaches_the_controls(self):
        params = {
            "demo.on_off": param("demo.on_off", "on_off", "bool", 0, 1, 1),
            "demo.level": param("demo.level", "level", "float", 0.0, 1.0, 0.01),
        }
        specs = [{
            "prefix": "demo.",
            "title": "Demo",
            "widgets": [{"match": r"demo[.]level$", "widget": "choice"}],
        }]
        groups = controls.build_groups(specs, params)
        by_id = {c["id"]: c for group in groups for c in group["controls"]}
        self.assertIn("demo.level", by_id)
        self.assertEqual(by_id["demo.level"]["kind"], "choice")


if __name__ == "__main__":
    unittest.main()
