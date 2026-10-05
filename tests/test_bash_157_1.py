"""Regression test for issue #15701: coarse-pointer tap targets in preview.html.

The final `@media (pointer: coarse)` block must raise the small controls to a
44px tap target, and the base rules outside that block must be left alone.
"""

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PREVIEW = os.path.join(os.path.dirname(HERE), "preview.html")

# The block runs from its opening brace to the matching close at column 0.
COARSE_RE = re.compile(
    r"@media\s*\(\s*pointer:\s*coarse\s*\)\s*\{(?P<body>.*?)\n\}",
    re.DOTALL,
)


def read_preview():
    with open(PREVIEW, "r", encoding="utf-8") as fh:
        return fh.read()


def coarse_block(text):
    matches = COARSE_RE.findall(text)
    if not matches:
        raise AssertionError("no @media (pointer: coarse) block found in preview.html")
    # The interaction-states block is the last one in the file.
    return matches[-1]


def rule_body(block, selector):
    """Return the declaration text of `selector { ... }` inside `block`."""
    pattern = re.compile(
        r"(?:^|\n)\s*" + re.escape(selector) + r"\s*\{(?P<decls>[^}]*)\}",
        re.DOTALL,
    )
    match = pattern.search(block)
    if not match:
        raise AssertionError("no rule for %r in the coarse-pointer block" % selector)
    return match.group("decls")


def has_44px(decls):
    """True if the declarations set a min-height or height of 44px."""
    for prop in ("min-height", "height"):
        if re.search(
            r"(?:^|;)\s*" + prop + r"\s*:\s*44px\s*(?:;|$)",
            decls,
        ):
            return True
    return False


class CoarsePointerTargets(unittest.TestCase):
    def setUp(self):
        self.text = read_preview()
        self.block = coarse_block(self.text)

    def test_block_is_the_last_one(self):
        # Sanity: the extracted block is the interaction-states section.
        self.assertIn(".mini", self.block)
        self.assertIn(".bank", self.block)

    def test_mini_is_44px(self):
        decls = rule_body(self.block, ".mini")
        self.assertTrue(has_44px(decls), ".mini must be 44px tall: %r" % decls)
        self.assertRegex(decls, r"min-width\s*:\s*44px")

    def test_bank_is_44px(self):
        decls = rule_body(self.block, ".bank")
        self.assertTrue(has_44px(decls), ".bank must be 44px tall: %r" % decls)

    def test_takes_badge_action_is_44px(self):
        decls = rule_body(self.block, ".takes__badge.is-action")
        self.assertTrue(
            has_44px(decls), ".takes__badge.is-action must be 44px tall: %r" % decls
        )
        self.assertRegex(decls, r"display\s*:\s*inline-flex")
        self.assertRegex(decls, r"align-items\s*:\s*center")

    def test_importer_close_is_44px(self):
        decls = rule_body(self.block, ".importer__close")
        self.assertRegex(decls, r"width\s*:\s*44px")
        self.assertRegex(decls, r"height\s*:\s*44px")

    def test_dry_toggle_is_44px(self):
        decls = rule_body(self.block, ".dry-toggle")
        self.assertTrue(has_44px(decls), ".dry-toggle must be 44px tall: %r" % decls)

    def test_importer_check_is_44px(self):
        decls = rule_body(self.block, ".importer__check")
        self.assertTrue(
            has_44px(decls), ".importer__check must be 44px tall: %r" % decls
        )

    def test_base_rules_unchanged(self):
        # The base .mini rule still exists with its original 26px min-height.
        self.assertIn(".mini {", self.text)
        self.assertIn("min-height: 26px", self.text)
        base = re.search(r"\.mini\s*\{[^}]*\}", self.text, re.DOTALL)
        self.assertIsNotNone(base)
        self.assertIn("min-height: 26px", base.group(0))

    def test_base_bank_unchanged(self):
        base = re.search(r"\.bank\s*\{[^}]*\}", self.text, re.DOTALL)
        self.assertIsNotNone(base)
        self.assertIn("min-height: 28px", base.group(0))


if __name__ == "__main__":
    unittest.main()
