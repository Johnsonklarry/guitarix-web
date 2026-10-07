import os
import re
import unittest

APP_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'app.js')


def read_app():
    with open(APP_JS, 'r', encoding='utf-8') as fh:
        return fh.read()


def build_discrete_body(src):
    """Return the source of buildDiscrete(ctrl) up to the next top-level def."""
    start = src.index('function buildDiscrete(ctrl)')
    rest = src[start:]
    end = rest.find('\nfunction ', 1)
    return rest if end == -1 else rest[:end]


class BuildDiscreteUnifiedDispatchTest(unittest.TestCase):
    """Issue #26052: the dropdown and the switch built by buildDiscrete must
    dispatch through the shared command() adapter with the same payload shape
    { id: ctrl.id, value: value }."""

    def setUp(self):
        self.src = read_app()
        self.body = build_discrete_body(self.src)

    def test_build_discrete_exists(self):
        self.assertIn('function buildDiscrete(ctrl)', self.src)

    def test_select_change_uses_command_adapter(self):
        self.assertIn("command(ctrl.id, value, 'discrete')", self.body)

    def test_switch_click_uses_command_adapter(self):
        self.assertIn("command(ctrl.id, next, 'discrete')", self.body)

    def test_no_direct_emit_in_build_discrete(self):
        # neither branch may bypass the adapter with a raw socket.emit
        self.assertNotIn("socket.emit('set_param'", self.body)

    def test_payload_shape_is_consistent(self):
        # both branches funnel through command(id, value, 'discrete'), which
        # emits { id: id, value: value } -- assert the adapter contract holds
        adapter = self.src.split('function command(id, value, kind)', 1)[1]
        adapter = adapter.split('\n}', 1)[0]
        self.assertIn("socket.emit('set_param', { id: id, value: value })", adapter)
        self.assertIn("send(id, value)", adapter)

    def test_select_value_derived_from_option(self):
        # the select branch must resolve the option before dispatching
        self.assertRegex(
            self.body,
            r"const value = sel\._kind === 'key' && o\.key != null \? o\.key : o\.value;",
        )

    def test_switch_value_is_toggle_next(self):
        # the switch branch must compute the next on/off value before dispatch
        self.assertRegex(
            self.body,
            r"const next = sw\.classList\.contains\('is-on'\) \? 0 : 1;",
        )

    def test_apply_toggle_still_updates_switch_state(self):
        # the switch branch keeps its optimistic local update
        self.assertIn('applyToggle(ctrl.id, next)', self.body)


if __name__ == '__main__':
    unittest.main()
