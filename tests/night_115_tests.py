#!/usr/bin/env python3
"""
Regression tests for the broadcast styling migration (issue #115).

    python3 tests/night_115_tests.py

These are markup and stylesheet checks only: no Flask app, no guitarix, no
JACK. They pin the two things the issue asks for -- broadcast.html adopts the
shared components (style.css loaded before broadcast.css, shared class names
kept, focus hooks present) and broadcast.css is reduced to broadcast-specific
styling (no @import of style.css, themes still driven by custom properties,
accessible controls and narrow-width rules still there).
"""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

HTML = os.path.join(ROOT, "templates", "broadcast.html")
CSS = os.path.join(ROOT, "static", "broadcast.css")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class BroadcastMarkup(unittest.TestCase):
    def setUp(self):
        self.html = read(HTML)

    def test_loads_shared_style_before_broadcast_css(self):
        shared = self.html.find("style.css")
        own = self.html.find("broadcast.css")
        self.assertNotEqual(shared, -1, "style.css is not loaded at all")
        self.assertNotEqual(own, -1, "broadcast.css is not loaded at all")
        self.assertLess(shared, own,
                        "style.css must load before broadcast.css")

    def test_theme_kit_still_loads_first(self):
        themes = self.html.find("theme-kit/themes.css")
        shared = self.html.find("style.css")
        self.assertNotEqual(themes, -1, "the theme kit is gone")
        self.assertLess(themes, shared, "the theme kit must load first")

    def test_keeps_shared_component_classes(self):
        for cls in ("pilot", "status", "preset", "takes", "listen"):
            self.assertIn(cls, self.html, "shared class %r is missing" % cls)

    def test_keeps_the_markup_contract_broadcast_js_reads(self):
        for ident in ("pilot", "status", "bank", "preset", "rolling",
                      "takes", "stream"):
            self.assertIn('id="%s"' % ident, self.html,
                          "broadcast.js reads #%s" % ident)

    def test_stream_is_still_a_plain_audio_element(self):
        self.assertIn("<audio", self.html)
        self.assertIn('src="/monitor.mp3"', self.html)
        self.assertIn("controls", self.html)

    def test_no_controls_were_added(self):
        # The page can change nothing: no buttons, no forms, no inputs.
        for tag in ("<button", "<form", "<input", "<select", "<textarea"):
            self.assertNotIn(tag, self.html, "%s appeared on the page" % tag)


class BroadcastStylesheet(unittest.TestCase):
    def setUp(self):
        self.css = read(CSS)

    def test_does_not_import_the_shared_stylesheet(self):
        self.assertNotIn("@import", self.css,
                         "broadcast.css must not @import style.css any more")

    def test_stays_theme_driven(self):
        for prop in ("--bg", "--edge", "--lamp", "--panel", "--focus-ring"):
            self.assertIn("var(%s" % prop, self.css,
                          "themes are bypassed: %s is unused" % prop)

    def test_keeps_focus_hooks(self):
        self.assertIn(":focus-visible", self.css,
                      "no focus-visible rule for the accessible controls")
        self.assertIn("var(--focus-ring)", self.css,
                      "focus-visible does not use the shared focus ring")

    def test_keeps_narrow_width_rules(self):
        self.assertIn("@media (max-width: 480px)", self.css)
        self.assertIn("@media (max-width: 360px)", self.css)

    def test_keeps_the_pilot_live_state_broadcast_js_toggles(self):
        self.assertIn(".pilot.is-on", self.css,
                      "broadcast.js toggles is-on, not is-live")

    def test_keeps_the_audio_control_sized(self):
        self.assertIn("audio", self.css,
                      "the stream control has no sizing rule")


if __name__ == "__main__":
    unittest.main(verbosity=2)
