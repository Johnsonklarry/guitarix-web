"""Regression guard for the pre-existing rig and broadcast styling.

Issue #26204: later edits (theme kit, markup hooks, rule pruning) must not
remove or rename the baseline selectors and key declarations that the rig
and the public broadcast view already relied on.

Read-only: parses the two stylesheets as plain text. No DOM, server,
network, audio hardware, or credentials are involved.
"""

import os
import re
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STYLE_CSS = os.path.join(REPO_ROOT, "static", "style.css")
BROADCAST_CSS = os.path.join(REPO_ROOT, "static", "broadcast.css")


def read_css(path):
    """Return the stylesheet text, or fail loudly if it is missing."""
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def rule_body(css, selector):
    """Return the declaration block for `selector`, or None if absent.

    Matches the selector only when it appears as a whole selector token
    (start of a rule or after a comma), so `.cab` does not match `.cab__inner`.
    """
    pattern = re.compile(
        r"(?:^|[},])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}",
        re.MULTILINE,
    )
    match = pattern.search(css)
    if match is None:
        return None
    return match.group(1)


def has_declaration(body, declaration):
    """True when `declaration` appears in `body` as a whole declaration."""
    if body is None:
        return False
    pattern = re.compile(
        r"(?:^|;)\s*" + re.escape(declaration) + r"\s*(?:;|$)",
        re.MULTILINE,
    )
    return pattern.search(body) is not None


class ExistingStyleGuardTest(unittest.TestCase):
    """Baseline rig and broadcast selectors must survive later edits."""

    @classmethod
    def setUpClass(cls):
        cls.style_css = read_css(STYLE_CSS)
        cls.broadcast_css = read_css(BROADCAST_CSS)

    # ------------------------------------------------------------ rig (style.css)

    def test_style_css_is_non_empty(self):
        self.assertGreater(len(self.style_css), 0)

    def test_rig_cab_selector_present(self):
        body = rule_body(self.style_css, ".cab")
        self.assertIsNotNone(body, ".cab selector missing from static/style.css")

    def test_rig_cab_key_declarations(self):
        body = rule_body(self.style_css, ".cab")
        for declaration in (
            "display: flex",
            "flex-direction: column",
            "width: 100%",
            "max-width: 680px",
            "border-radius: var(--r-lg)",
        ):
            self.assertTrue(
                has_declaration(body, declaration),
                ".cab lost declaration %r" % declaration,
            )

    def test_rig_preset_selector_present(self):
        body = rule_body(self.style_css, ".preset")
        self.assertIsNotNone(body, ".preset selector missing from static/style.css")

    def test_rig_preset_key_declarations(self):
        body = rule_body(self.style_css, ".preset")
        for declaration in (
            "min-height: 74px",
            "max-height: 152px",
            "border-radius: var(--r-sm)",
        ):
            self.assertTrue(
                has_declaration(body, declaration),
                ".preset lost declaration %r" % declaration,
            )

    def test_rig_band_fader_selector_present(self):
        body = rule_body(self.style_css, ".band__fader")
        self.assertIsNotNone(
            body, ".band__fader selector missing from static/style.css"
        )

    def test_rig_band_fader_key_declarations(self):
        body = rule_body(self.style_css, ".band__fader")
        for declaration in (
            "position: relative",
            "width: 40px",
            "height: 168px",
        ):
            self.assertTrue(
                has_declaration(body, declaration),
                ".band__fader lost declaration %r" % declaration,
            )

    # ------------------------------------------------------ broadcast (broadcast.css)

    def test_broadcast_css_is_non_empty(self):
        self.assertGreater(len(self.broadcast_css), 0)

    def test_broadcast_cab_selector_present(self):
        body = rule_body(self.broadcast_css, ".cab")
        self.assertIsNotNone(
            body, ".cab selector missing from static/broadcast.css"
        )

    def test_broadcast_cab_key_declarations(self):
        body = rule_body(self.broadcast_css, ".cab")
        for declaration in (
            "width: 100%",
            "max-width: 500px",
            "border-radius: var(--r-lg)",
        ):
            self.assertTrue(
                has_declaration(body, declaration),
                "broadcast .cab lost declaration %r" % declaration,
            )

    def test_broadcast_preset_selector_present(self):
        body = rule_body(self.broadcast_css, ".preset")
        self.assertIsNotNone(
            body, ".preset selector missing from static/broadcast.css"
        )

    def test_broadcast_preset_key_declarations(self):
        body = rule_body(self.broadcast_css, ".preset")
        for declaration in (
            "min-height: 74px",
            "max-height: 152px",
            "cursor: default",
        ):
            self.assertTrue(
                has_declaration(body, declaration),
                "broadcast .preset lost declaration %r" % declaration,
            )

    def test_broadcast_status_selector_present(self):
        body = rule_body(self.broadcast_css, ".status")
        self.assertIsNotNone(
            body, ".status selector missing from static/broadcast.css"
        )

    def test_broadcast_status_key_declarations(self):
        body = rule_body(self.broadcast_css, ".status")
        for declaration in (
            "display: flex",
            "align-items: center",
            "justify-content: center",
        ):
            self.assertTrue(
                has_declaration(body, declaration),
                "broadcast .status lost declaration %r" % declaration,
            )

    # ------------------------------------------------------------ helper sanity

    def test_rule_body_does_not_match_prefixed_selector(self):
        css = ".cab__inner { display: flex; }"
        self.assertIsNone(rule_body(css, ".cab"))

    def test_rule_body_matches_selector_after_comma(self):
        css = ".a, .cab { display: flex; }"
        self.assertIsNotNone(rule_body(css, ".cab"))

    def test_has_declaration_requires_whole_declaration(self):
        self.assertFalse(has_declaration("max-width: 680pxx", "max-width: 680px"))
        self.assertTrue(has_declaration("max-width: 680px", "max-width: 680px"))


if __name__ == "__main__":
    unittest.main()
