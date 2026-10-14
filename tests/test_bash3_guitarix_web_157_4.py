import re
import unittest
from pathlib import Path


PREVIEW = Path(__file__).resolve().parents[1] / "preview.html"


def css_rule(css, selector):
    pattern = re.compile(
        r"(?<![\w.-])" + re.escape(selector) + r"\s*\{([^{}]*)\}"
    )
    matches = pattern.findall(css)
    if not matches:
        raise AssertionError(f"No CSS rule found for {selector}")
    return matches


def minimum_pixels(declarations, property_name):
    match = re.search(
        rf"(?:^|;)\s*{re.escape(property_name)}\s*:\s*(\d+)px\s*(?:;|$)",
        declarations,
    )
    return int(match.group(1)) if match else 0


class TapTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = PREVIEW.read_text(encoding="utf-8")
        cls.css = html.split("<style>", 1)[1].split("</style>", 1)[0]

    def test_dry_toggle_parent_is_at_least_44_pixels_in_both_dimensions(self):
        declarations = css_rule(self.css, ".dry-toggle")[0]
        self.assertGreaterEqual(minimum_pixels(declarations, "min-height"), 44)
        self.assertGreaterEqual(minimum_pixels(declarations, "min-width"), 44)

    def test_action_badge_is_at_least_44_pixels_in_both_dimensions(self):
        declarations = css_rule(self.css, ".takes__badge.is-action")[0]
        self.assertGreaterEqual(minimum_pixels(declarations, "min-height"), 44)
        self.assertGreaterEqual(minimum_pixels(declarations, "min-width"), 44)


if __name__ == "__main__":
    unittest.main()
