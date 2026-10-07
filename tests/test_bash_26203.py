"""Read-only regression guards for the existing rig and broadcast CSS.

Issue #26203: the rig stylesheet (static/style.css) and the broadcast
stylesheet (static/broadcast.css) already define a set of baseline
selectors and declarations.  This module asserts those baselines are
still present so that a later refactor cannot silently delete or rename
them.

The tests read the CSS files directly as text.  There is no DOM, no
server, no network, no audio hardware and no credentials involved.
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STYLE_CSS = os.path.join(REPO_ROOT, "static", "style.css")
BROADCAST_CSS = os.path.join(REPO_ROOT, "static", "broadcast.css")


def read_css(path):
    """Return the stylesheet text, or fail loudly if it is missing."""
    if not os.path.isfile(path):
        raise AssertionError("stylesheet not found: %s" % path)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def strip_comments(css):
    """Remove /* ... */ comments so selector checks see real rules only."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def rule_bodies(css):
    """Map each selector text to the concatenated bodies of its rules.

    Only flat rules are handled (no nested at-rule bodies), which is all
    the baseline selectors below need.  A selector that appears in more
    than one rule has its bodies joined with a newline.
    """
    text = strip_comments(css)
    bodies = {}
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        selector = " ".join(match.group(1).split())
        body = match.group(2)
        if selector in bodies:
            bodies[selector] = bodies[selector] + "\n" + body
        else:
            bodies[selector] = body
    return bodies


def declarations(body):
    """Return the set of property names declared in a rule body."""
    names = set()
    for chunk in body.split(";"):
        if ":" not in chunk:
            continue
        name = chunk.split(":", 1)[0].strip()
        if name:
            names.add(name)
    return names


class ExistingStyleGuardTest(unittest.TestCase):
    """The pre-existing rig and broadcast baselines must survive."""

    @classmethod
    def setUpClass(cls):
        cls.style_css = read_css(STYLE_CSS)
        cls.broadcast_css = read_css(BROADCAST_CSS)
        cls.style_rules = rule_bodies(cls.style_css)
        cls.broadcast_rules = rule_bodies(cls.broadcast_css)

    # ------------------------------------------------------------ rig

    def test_style_css_defines_baseline_selectors(self):
        for selector in (
            ".cab",
            ".grille",
            ".transport",
            ".rec-btn",
            '.band__fader input[type="range"]',
        ):
            self.assertIn(
                selector,
                self.style_rules,
                "static/style.css no longer defines %r" % selector,
            )

    def test_style_css_cab_is_a_rounded_surface(self):
        body = self.style_rules[".cab"]
        self.assertIn("border-radius", declarations(body))
        self.assertIn("background", declarations(body))

    def test_style_css_grille_lays_out_its_children(self):
        body = self.style_rules[".grille"]
        self.assertIn("display", declarations(body))

    def test_style_css_transport_is_a_flex_row(self):
        body = self.style_rules[".transport"]
        self.assertIn("display", declarations(body))
        self.assertIn("flex", body)

    def test_style_css_rec_btn_is_a_control(self):
        body = self.style_rules[".rec-btn"]
        self.assertIn("cursor", declarations(body))

    def test_style_css_band_fader_range_is_styled(self):
        body = self.style_rules['.band__fader input[type="range"]']
        self.assertTrue(
            declarations(body),
            "the band fader range rule has an empty body",
        )

    def test_style_css_keeps_responsive_media_queries(self):
        self.assertIn("@media", self.style_css)

    # ------------------------------------------------------ broadcast

    def test_broadcast_css_defines_baseline_selectors(self):
        for selector in (
            ".cab",
            ".grille",
            ".status",
            ".listen",
            ".listen.is-on .listen__lamp",
        ):
            self.assertIn(
                selector,
                self.broadcast_rules,
                "static/broadcast.css no longer defines %r" % selector,
            )

    def test_broadcast_css_cab_is_a_rounded_surface(self):
        body = self.broadcast_rules[".cab"]
        self.assertIn("border-radius", declarations(body))
        self.assertIn("background", declarations(body))

    def test_broadcast_css_grille_lays_out_its_children(self):
        body = self.broadcast_rules[".grille"]
        self.assertIn("display", declarations(body))

    def test_broadcast_css_status_is_a_flex_row(self):
        body = self.broadcast_rules[".status"]
        self.assertIn("display", declarations(body))
        self.assertIn("flex", body)

    def test_broadcast_css_listen_is_a_control(self):
        body = self.broadcast_rules[".listen"]
        self.assertIn("cursor", declarations(body))

    def test_broadcast_css_listen_on_lamp_is_lit(self):
        body = self.broadcast_rules[".listen.is-on .listen__lamp"]
        self.assertIn("background", declarations(body))
        self.assertIn("box-shadow", declarations(body))

    def test_broadcast_css_imports_the_shared_style_layer(self):
        self.assertIn('@import url("./style.css")', self.broadcast_css)


if __name__ == "__main__":
    unittest.main()
