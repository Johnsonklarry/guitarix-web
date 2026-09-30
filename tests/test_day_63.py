#!/usr/bin/env python3
"""
Day 63: the clickable Settings badge in the takes list must be keyboard
accessible (role=button, tabindex=0, Enter/Space activation, visible focus).

    python3 tests/test_day_63.py

Static checks on static/app.js and static/style.css; no browser, guitarix or
network needed.
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def settings_badge_block():
    js = read("static", "app.js")
    start = js.index("function takeNameCell(")
    end = js.index("function smallBadge(", start)
    body = js[start:end]
    a = body.index("smallBadge('Settings'")
    return body[a:]


class TestSettingsBadgeKeyboard(unittest.TestCase):
    def setUp(self):
        self.block = settings_badge_block()

    def test_has_button_role(self):
        self.assertRegex(self.block, r"setAttribute\(\s*'role'\s*,\s*'button'\s*\)")

    def test_is_focusable(self):
        self.assertTrue(
            re.search(r"\.tabIndex\s*=\s*0\b", self.block)
            or re.search(r"setAttribute\(\s*'tabindex'\s*,\s*'0'\s*\)", self.block))

    def test_enter_and_space_activate(self):
        m = re.search(r"addEventListener\(\s*'keydown'.*", self.block, re.S)
        self.assertIsNotNone(m, "no keydown handler on the Settings badge")
        handler = m.group(0)
        self.assertIn("'Enter'", handler)
        self.assertIn("' '", handler)
        self.assertIn("loadTakeSettings(", handler)
        self.assertIn("preventDefault()", handler)

    def test_click_still_loads(self):
        self.assertRegex(self.block, r"addEventListener\(\s*'click'.*loadTakeSettings")

    def test_focus_style_exists(self):
        css = read("static", "style.css")
        self.assertRegex(css, r"\.takes__badge\.is-action:focus-visible\s*\{[^}]*outline")


if __name__ == "__main__":
    unittest.main()
