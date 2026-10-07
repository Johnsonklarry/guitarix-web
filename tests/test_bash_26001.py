"""Issue #260.1 -- one ControlRegistry binding map for the control surface.

static/app.js is browser code, so these checks are made against its source.
What the issue asks for is structural: the sliders, readouts, switches and
selects maps are consulted through one registry, that registry is pruned when
a container is rebuilt, and applyValues() dispatches through it instead of
branching across the separate maps.
"""

import re
import unittest
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "static" / "app.js"


def read_app_js():
    return APP_JS.read_text(encoding="utf-8")


def function_body(text, name):
    """Return `function name(...) { ... }` up to its closing column-0 brace."""
    header = "function " + name + "("
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith(header):
            start = index
            break
    if start is None:
        raise AssertionError("app.js has no `" + header + "...)` definition")
    body = [lines[start]]
    for line in lines[start + 1:]:
        body.append(line)
        if line.startswith("}"):
            return "\n".join(body)
    raise AssertionError("unterminated body for " + header)


class ControlRegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = read_app_js()

    def body(self, name):
        return function_body(self.source, name)

    def test_registry_is_a_single_map_keyed_by_parameter_id(self):
        self.assertEqual(
            1,
            len(re.findall(r"^const\s+ControlRegistry\s*=\s*new\s+Map\(\);$",
                           self.source, re.M)),
        )
        self.assertIn("ControlRegistry.set(id, binding)", self.body("bindControl"))

    def test_make_range_registers_its_fader_and_readout(self):
        body = self.body("makeRange")
        self.assertIn("bindControl(ctrl.id,", body)
        self.assertIn("kind: 'range'", body)
        self.assertIn("el: input", body)
        self.assertIn("readout: valueEl", body)

    def test_build_discrete_registers_switches_and_selects(self):
        body = self.body("buildDiscrete")
        self.assertEqual(2, body.count("bindControl(ctrl.id,"))
        self.assertIn("kind: 'switch'", body)
        self.assertIn("kind: 'select'", body)
        self.assertIn("apply:", body)

    def test_release_controls_prunes_the_registry(self):
        body = self.body("releaseControls")
        self.assertIn("ControlRegistry.forEach(", body)
        self.assertIn("ControlRegistry.delete(id)", body)
        self.assertIn("into.contains(binding.el)", body)
        # the per-map sweep it replaces is still there, so nothing is orphaned
        self.assertIn("[sliders, readouts, switches, selects].forEach(", body)

    def test_apply_values_dispatches_through_the_registry(self):
        body = self.body("applyValues")
        self.assertIn("const bound = ControlRegistry.get(id)", body)
        self.assertIn("bound.apply(values[id], remote)", body)
        self.assertNotIn("if (switches[id])", body)
        self.assertNotIn("if (selects[id])", body)

    def test_apply_values_still_defers_to_a_held_fader(self):
        body = self.body("applyValues")
        self.assertIn("const input = bound ? bound.el : sliders[id]", body)
        self.assertIn("dragging.has(id)", body)

    def test_render_groups_releases_offscreen_bindings(self):
        self.assertIn("releaseControls(into)", self.body("renderGroups"))

    def test_render_groups_registers_the_bypass_switch(self):
        # The group's bypass switch lives in the switches map, which applyToggle()
        # reads. applyValues() does not: it looks the id up in ControlRegistry, so
        # a bypass without a binding is a parameter whose remote updates go nowhere.
        body = self.body("renderGroups")
        self.assertIn("switches[group.toggle] = sw;", body)
        self.assertIn("bindControl(group.toggle,", body)
        self.assertIn("kind: 'switch'", body)
        self.assertIn("applyToggle(group.toggle, value)", body)

    def test_every_id_written_to_a_control_map_is_also_bound(self):
        # applyValues() dispatches through ControlRegistry alone, so a control
        # recorded only in one of the older maps is invisible to remote updates.
        for name in ("makeRange", "buildDiscrete", "renderGroups"):
            body = self.body(name)
            written = re.findall(
                r"\b(?:sliders|readouts|switches|selects)\[([^\]]+)\]\s*=", body)
            self.assertTrue(written, name + " records no controls")
            for key in written:
                key = key.strip()
                self.assertIn("bindControl(" + key + ",", body,
                              name + " records " + key + " in a map without binding it")


if __name__ == "__main__":
    unittest.main()
