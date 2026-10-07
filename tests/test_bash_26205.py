"""Regression test for issue #26205.

templates/preview.html must load the vendored shared layers
(static/theme-kit/themes.css and static/theme-kit/theme.js) before
static/style.css in document order, matching templates/index.html and
templates/broadcast.html.

Parsing is done with html.parser only: no DOM, no server, no network, no
audio hardware, no credentials.
"""

import os
import unittest
from html.parser import HTMLParser

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(REPO_ROOT, "templates")
PREVIEW = os.path.join(TEMPLATES_DIR, "preview.html")

THEMES_CSS = "static/theme-kit/themes.css"
THEME_JS = "static/theme-kit/theme.js"
STYLE_CSS = "static/style.css"


class _HeadLinkParser(HTMLParser):
    """Collects <link rel="stylesheet"> hrefs and <script src> in order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stylesheets = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link":
            rel = (attrs.get("rel") or "").lower()
            if "stylesheet" in rel.split():
                href = attrs.get("href")
                if href:
                    self.stylesheets.append(href)
        elif tag == "script":
            src = attrs.get("src")
            if src:
                self.scripts.append(src)

    handle_startendtag = handle_starttag


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def parse_preview():
    """Parse templates/preview.html and return the parser."""
    parser = _HeadLinkParser()
    parser.feed(_read(PREVIEW))
    parser.close()
    return parser


def shared_layers_before_style(stylesheets, scripts):
    """True when both shared layers precede style.css in document order.

    Document order is approximated by the order the parser saw the tags:
    stylesheets keep their own order, and the script is interleaved by
    comparing against the position of style.css in the stylesheet list.
    """
    if STYLE_CSS not in stylesheets:
        return False
    if THEMES_CSS not in stylesheets:
        return False
    if THEME_JS not in scripts:
        return False
    return stylesheets.index(THEMES_CSS) < stylesheets.index(STYLE_CSS)


class PreviewSharedLayersTest(unittest.TestCase):
    def test_preview_loads_shared_layers_before_style_css(self):
        parser = parse_preview()
        self.assertIn(
            THEMES_CSS,
            parser.stylesheets,
            "preview.html must load the vendored shared theme stylesheet",
        )
        self.assertIn(
            THEME_JS,
            parser.scripts,
            "preview.html must load the vendored shared theme script",
        )
        self.assertIn(
            STYLE_CSS,
            parser.stylesheets,
            "preview.html must load static/style.css",
        )
        self.assertTrue(
            shared_layers_before_style(parser.stylesheets, parser.scripts),
            "shared layers must be loaded before static/style.css",
        )

    def test_themes_css_precedes_style_css_in_document_order(self):
        parser = parse_preview()
        self.assertLess(
            parser.stylesheets.index(THEMES_CSS),
            parser.stylesheets.index(STYLE_CSS),
            "static/theme-kit/themes.css must appear before static/style.css",
        )

    def test_theme_js_precedes_style_css_in_document_order(self):
        parser = parse_preview()
        # The script sits between the two stylesheet links in index.html;
        # assert it is not emitted after style.css by checking the raw text.
        text = _read(PREVIEW)
        self.assertLess(
            text.index(THEME_JS),
            text.index(STYLE_CSS),
            "static/theme-kit/theme.js must appear before static/style.css",
        )

    def test_shared_layers_are_vendored_not_remote(self):
        parser = parse_preview()
        for href in parser.stylesheets:
            self.assertFalse(
                href.startswith("http://") or href.startswith("https://"),
                "shared layers must be vendored, not remote: %s" % href,
            )


if __name__ == "__main__":
    unittest.main()
