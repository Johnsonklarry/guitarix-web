"""Regression test for issue #164: preview.html loads the shared layers.

The page used to be a standalone document carrying its own copy of every
shared component rule. It now links the theme-kit token layer and the shared
stylesheet ahead of its own inline <style>, pulls in the theme picker script,
and drops the duplicated component rules (static/style.css supplies them).
Only the preview-specific rules stay inline.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "templates" / "preview.html"


class PreviewSharedLayers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.first_style = cls.html.index("<style>")
        # everything that loads before the page's own inline stylesheet
        cls.head = cls.html[: cls.first_style]
        found = re.search(r"<style>(.*?)</style>", cls.html, re.S)
        assert found is not None, "preview.html has no inline <style> block"
        cls.inline = found.group(1)

    def test_shared_stylesheets_load_first(self):
        theme = self.head.find("static/theme-kit/themes.css")
        shared = self.head.find("static/style.css")
        self.assertNotEqual(theme, -1, "themes.css link is missing from <head>")
        self.assertNotEqual(shared, -1, "style.css link is missing from <head>")
        self.assertLess(theme, shared, "themes.css must load before style.css")
        # both are in <head>, ahead of the inline <style> block
        self.assertLess(shared, self.first_style)

    def test_theme_script_loaded(self):
        self.assertIn("static/theme-kit/theme.js", self.html)

    def test_duplicate_component_rules_removed(self):
        # the base rules come from static/style.css now; only layout-scoped
        # rules (for example .toolbar__actions .act) may stay inline
        self.assertIsNone(
            re.search(r"(?m)^\.act\s*\{", self.inline),
            "the inline .act base rule should have been removed",
        )
        self.assertIsNone(
            re.search(r"(?m)^\.preset\s*\{", self.inline),
            "the inline .preset base rule should have been removed",
        )

    def test_preview_only_rules_kept(self):
        self.assertIn(".demo-bar", self.inline)
        self.assertIn(".reampbar", self.inline)

    def test_markup_keeps_shared_class_names(self):
        self.assertIn('class="act"', self.html)
        self.assertIn('class="pad"', self.html)
        # preset tiles are built by the page script; the prefix covers the
        # markup strings the script injects (preset__name / preset__tag)
        self.assertIn('class="preset', self.html)
        self.assertIn("className = 'preset'", self.html)


if __name__ == "__main__":
    unittest.main()
