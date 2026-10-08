import os
import re
import unittest

APP_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'static', 'app.js')


def read_app():
    with open(APP_JS, 'r', encoding='utf-8') as fh:
        return fh.read()


def extract_apply_values(src):
    """Return the source text of the applyValues function."""
    start = src.index('function applyValues(')
    depth = 0
    i = src.index('{', start)
    begin = i
    while i < len(src):
        ch = src[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return src[begin:i + 1]
        i += 1
    raise AssertionError('unterminated applyValues')


class ApplyValuesUnification(unittest.TestCase):
    def setUp(self):
        self.src = read_app()
        self.body = extract_apply_values(self.src)

    def test_function_exists(self):
        self.assertIn('function applyValues(values, remote)', self.src)

    def test_no_early_return_after_switch(self):
        # The switch branch must not `return` before the select/slider
        # branches get a chance to run.
        switch_block = re.search(
            r'if \(switches\[id\]\) \{(.*?)\n    \}',
            self.body,
            re.DOTALL,
        )
        self.assertIsNotNone(switch_block, 'switch branch not found')
        self.assertNotIn('return;', switch_block.group(1),
                         'switch branch still returns early')

    def test_no_early_return_after_select(self):
        select_block = re.search(
            r'if \(selects\[id\]\) \{(.*?)\n    \}',
            self.body,
            re.DOTALL,
        )
        self.assertIsNotNone(select_block, 'select branch not found')
        self.assertNotIn('return;', select_block.group(1),
                         'select branch still returns early')

    def test_all_three_maps_are_consulted(self):
        for name in ('switches[id]', 'selects[id]', 'sliders[id]'):
            self.assertIn(name, self.body,
                          'applyValues no longer consults %s' % name)

    def test_switch_branch_precedes_select_branch(self):
        self.assertLess(self.body.index('if (switches[id])'),
                        self.body.index('if (selects[id])'))

    def test_select_branch_precedes_slider_branch(self):
        self.assertLess(self.body.index('if (selects[id])'),
                        self.body.index('const input = sliders[id]'))

    def test_state_values_still_recorded(self):
        self.assertIn('state.values[id] = values[id];', self.body)

    def test_dragging_guard_preserved(self):
        self.assertIn('dragging.has(id)', self.body)

    def test_remote_glide_preserved(self):
        self.assertIn('glide(input, target)', self.body)

    def test_remote_flash_preserved(self):
        self.assertIn("flash(out, 'is-remote')", self.body)

    def test_switch_flash_preserved(self):
        self.assertIn("flash(switches[id], 'is-remote')", self.body)

    def test_select_flash_preserved(self):
        self.assertIn("flash(selects[id], 'is-remote')", self.body)

    def test_release_controls_still_covers_all_maps(self):
        # releaseControls must keep clearing every map that applyValues
        # can now touch, otherwise detached nodes leak.
        start = self.src.index('function releaseControls(')
        end = self.src.index('function renderGroups(', start)
        block = self.src[start:end]
        for name in ('sliders', 'readouts', 'switches', 'selects'):
            self.assertIn(name, block,
                          'releaseControls no longer clears %s' % name)


if __name__ == '__main__':
    unittest.main()
