"""Guard the baseline styling of the rig and broadcast views.

These tests read the real stylesheets and check that the selectors the rest
of the app depends on still carry their original declarations *inside their
own rule block*.  Searching the whole file for a declaration would pass even
if the rule had been deleted and the text left behind in a comment or in a
different selector, so each check is scoped to the block that owns it.

Offline and read-only: nothing here writes to disk or touches the network.
"""

import os
import re
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STYLE_CSS = os.path.join(ROOT, "static", "style.css")
BROADCAST_CSS = os.path.join(ROOT, "static", "broadcast.css")


def read_stylesheet(path):
    """Return the text of a stylesheet, failing loudly if it is missing."""
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def strip_comments(css):
    """Remove /* ... */ comments so commented-out rules can't satisfy a check."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def rule_blocks(css, selector):
    """Yield the declaration bodies of every rule whose selector list contains
    `selector` as a whole selector.

    A block is only yielded when the selector appears in the prelude of a rule
    (the text between the previous `}`/`{` and the opening `{`), so a mention
    of the selector inside a declaration value or a comment is ignored.
    """
    css = strip_comments(css)
    pattern = re.compile(r"([^{}]*)\{([^{}]*)\}", re.DOTALL)
    for match in pattern.finditer(css):
        prelude = match.group(1)
        selectors = [part.strip() for part in prelude.split(",")]
        if selector in selectors:
            yield match.group(2)


def declarations(block):
    """Parse a declaration body into a {property: value} mapping."""
    found = {}
    for chunk in block.split(";"):
        if ":" not in chunk:
            continue
        prop, _, value = chunk.partition(":")
        found[prop.strip().lower()] = value.strip()
    return found


def declaration_in(css, selector, prop, value):
    """True when some rule for `selector` sets `prop` to `value`."""
    for block in rule_blocks(css, selector):
        if declarations(block).get(prop) == value:
            return True
    return False


class ExistingStyleGuardTest(unittest.TestCase):
    """The rig and broadcast stylesheets keep their baseline geometry."""

    @classmethod
    def setUpClass(cls):
        cls.style = read_stylesheet(STYLE_CSS)
        cls.broadcast = read_stylesheet(BROADCAST_CSS)

    def test_style_cab_max_width(self):
        self.assertTrue(
            declaration_in(self.style, ".cab", "max-width", "680px"),
            ".cab in static/style.css must keep max-width: 680px",
        )

    def test_style_presets_two_columns(self):
        self.assertTrue(
            declaration_in(
                self.style, ".presets", "grid-template-columns", "repeat(2, 1fr)"
            ),
            ".presets in static/style.css must keep two columns",
        )

    def test_style_grille_background(self):
        self.assertTrue(
            declaration_in(self.style, ".grille", "background-color", "var(--bg)"),
            ".grille in static/style.css must keep background-color: var(--bg)",
        )

    def test_broadcast_imports_style(self):
        self.assertIn(
            '@import url("./style.css")',
            self.broadcast,
            "static/broadcast.css must import the shared stylesheet",
        )

    def test_broadcast_cab_max_width(self):
        self.assertTrue(
            declaration_in(self.broadcast, ".cab", "max-width", "500px"),
            ".cab in static/broadcast.css must keep max-width: 500px",
        )

    def test_broadcast_presets_single_column(self):
        self.assertTrue(
            declaration_in(self.broadcast, ".presets", "grid-template-columns", "1fr"),
            ".presets in static/broadcast.css must keep a single column",
        )

    def test_broadcast_grille_background(self):
        self.assertTrue(
            declaration_in(self.broadcast, ".grille", "background-color", "var(--bg)"),
            ".grille in static/broadcast.css must keep background-color: var(--bg)",
        )

    def test_guard_ignores_commented_out_rules(self):
        """A declaration that only survives inside a comment must not count."""
        css = "/* .cab { max-width: 680px; } */\n.cab { max-width: 100%; }"
        self.assertFalse(declaration_in(css, ".cab", "max-width", "680px"))
        self.assertTrue(declaration_in(css, ".cab", "max-width", "100%"))

    def test_guard_ignores_other_selectors(self):
        """The declaration must live in the named selector's own block."""
        css = ".other { max-width: 680px; }\n.cab { max-width: 100%; }"
        self.assertFalse(declaration_in(css, ".cab", "max-width", "680px"))


if __name__ == "__main__":
    unittest.main()
