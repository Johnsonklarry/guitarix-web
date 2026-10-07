"""Regression guard for issue #26206.

The vendored shared layers (the theme kit) have to reach the page before the
rig stylesheet, otherwise style.css cannot lean on the kit's custom properties
and the theme switch paints with the wrong colours. An earlier attempt at this
fix moved theme.js below style.css; that ordering is what this test rejects.

Read only: the template is opened as text and parsed with html.parser, so there
is no browser, server, DOM, network, audio hardware or credentials involved.
"""

import unittest
from html.parser import HTMLParser
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "index.html"

# The vendored shared layers, and the rig's own stylesheet.
SHARED_STYLESHEET = "theme-kit/themes.css"
SHARED_SCRIPT = "theme-kit/theme.js"
RIG_STYLESHEET = "style.css"

# The ordering the earlier fix produced and the review gate turned down:
# the rig stylesheet loading before the shared script.
REJECTED_ORDERING = (
    '<link rel="stylesheet" href="/static/theme-kit/themes.css">\n'
    '<link rel="stylesheet" href="/static/style.css">\n'
    '<script src="/static/theme-kit/theme.js"></script>\n'
)


class HeadAssetParser(HTMLParser):
    """Collects stylesheet links and script sources in document order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.assets = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "link":
            rel = (attributes.get("rel") or "").lower().split()
            href = attributes.get("href") or ""
            if href and "stylesheet" in rel:
                self.assets.append(href)
        elif tag == "script":
            src = attributes.get("src") or ""
            if src:
                self.assets.append(src)

    def handle_startendtag(self, tag, attrs):
        """Void elements such as <link> may arrive through either hook."""
        self.handle_starttag(tag, attrs)


def asset_order(markup):
    """Every external stylesheet and script reference, in document order."""
    parser = HeadAssetParser()
    parser.feed(markup)
    parser.close()
    return parser.assets


def position_of(assets, needle):
    """Index of the first reference whose URL contains *needle*, else None."""
    for index, reference in enumerate(assets):
        if needle in reference:
            return index
    return None


class SharedLayerOrderTest(unittest.TestCase):
    """Issue #26206: templates/index.html loads the shared layers first."""

    def setUp(self):
        self.markup = TEMPLATE.read_text(encoding="utf-8")
        self.assets = asset_order(self.markup)

    def position(self, needle, assets=None):
        """Index of *needle* in *assets*, falling back to the template's."""
        position = position_of(self.assets if assets is None else assets, needle)
        self.assertIsNotNone(
            position,
            "templates/index.html no longer references %s" % needle,
        )
        return position

    def assert_layer_precedes_the_rig_stylesheet(self, needle, assets):
        """The one ordering check shared by every test below."""
        self.assertLess(
            self.position(needle, assets),
            self.position(RIG_STYLESHEET, assets),
            "%s must load before %s" % (needle, RIG_STYLESHEET),
        )

    def test_shared_stylesheet_loads_before_the_rig_stylesheet(self):
        self.assert_layer_precedes_the_rig_stylesheet(SHARED_STYLESHEET, self.assets)

    def test_shared_script_loads_before_the_rig_stylesheet(self):
        self.assert_layer_precedes_the_rig_stylesheet(SHARED_SCRIPT, self.assets)

    def test_each_shared_layer_is_referenced_exactly_once(self):
        for needle in (SHARED_STYLESHEET, SHARED_SCRIPT):
            matches = [asset for asset in self.assets if needle in asset]
            self.assertEqual(
                len(matches),
                1,
                "expected exactly one %s reference, found %r" % (needle, matches),
            )

    def test_guard_rejects_the_rejected_ordering(self):
        """The arrangement the earlier attempt shipped must not pass.

        The real ordering check is run against the rejected markup and has to
        fail there: if it were weakened, removed or inverted, this test would
        stop seeing an AssertionError and start failing itself.
        """
        rejected = asset_order(REJECTED_ORDERING)
        with self.assertRaises(AssertionError):
            self.assert_layer_precedes_the_rig_stylesheet(SHARED_SCRIPT, rejected)


if __name__ == "__main__":
    unittest.main()
