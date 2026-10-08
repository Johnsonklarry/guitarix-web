"""Regression test for issue #26205.

templates/preview.html must load the vendored shared layers
(static/theme-kit/themes.css and static/theme-kit/theme.js) before its own
static/style.css, matching templates/index.html and templates/broadcast.html.

Parsing is done with html.parser only: no DOM, no server, no network, no
audio hardware and no credentials. The template is read from the repository
tree (read-only) and the assertions go through the parsed page text, i.e.
through the template entry point, not only through a helper.
"""

import os
import unittest
from html.parser import HTMLParser

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(REPO_ROOT, "templates")

PREVIEW = os.path.join(TEMPLATES, "preview.html")
INDEX = os.path.join(TEMPLATES, "index.html")

THEMES_CSS = "theme-kit/themes.css"
THEME_JS = "theme-kit/theme.js"
STYLE_CSS = "style.css"


class _LinkCollector(HTMLParser):
    """Collects <link rel="stylesheet"> hrefs and <script src> values in order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stylesheets = []   # hrefs of stylesheet links, document order
        self.scripts = []       # srcs of scripts, document order
        self.order = []         # ("css", href) / ("js", src) in document order

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link":
            rel = (attrs.get("rel") or "").lower()
            href = attrs.get("href")
            if href and "stylesheet" in rel.split():
                self.stylesheets.append(href)
                self.order.append(("css", href))
        elif tag == "script":
            src = attrs.get("src")
            if src:
                self.scripts.append(src)
                self.order.append(("js", src))


def _parse(path):
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    parser = _LinkCollector()
    parser.feed(text)
    parser.close()
    return parser


def _index_of(haystack, needle):
    """Index of the first entry in haystack containing needle, or -1."""
    for i, item in enumerate(haystack):
        if needle in item:
            return i
    return -1


class SharedLayerOrderTest(unittest.TestCase):
    """The shared layers must come before the page's own stylesheet."""

    def test_preview_loads_themes_css_before_style_css(self):
        page = _parse(PREVIEW)
        themes = _index_of(page.stylesheets, THEMES_CSS)
        style = _index_of(page.stylesheets, STYLE_CSS)
        self.assertNotEqual(themes, -1, "preview.html does not load " + THEMES_CSS)
        self.assertNotEqual(style, -1, "preview.html does not load " + STYLE_CSS)
        self.assertLess(
            themes,
            style,
            "preview.html loads %s after %s" % (THEMES_CSS, STYLE_CSS),
        )

    def test_preview_loads_theme_js_before_style_css(self):
        page = _parse(PREVIEW)
        style = _index_of(page.stylesheets, STYLE_CSS)
        self.assertNotEqual(style, -1, "preview.html does not load " + STYLE_CSS)
        js = _index_of(page.scripts, THEME_JS)
        self.assertNotEqual(js, -1, "preview.html does not load " + THEME_JS)
        # Compare positions in the combined document order.
        js_pos = _index_of(
            ["%s:%s" % (kind, value) for kind, value in page.order],
            THEME_JS,
        )
        css_pos = _index_of(
            ["%s:%s" % (kind, value) for kind, value in page.order],
            STYLE_CSS,
        )
        self.assertNotEqual(js_pos, -1)
        self.assertNotEqual(css_pos, -1)
        self.assertLess(
            js_pos,
            css_pos,
            "preview.html loads %s after %s" % (THEME_JS, STYLE_CSS),
        )

    def test_preview_loads_both_shared_layers(self):
        page = _parse(PREVIEW)
        self.assertIn(
            THEMES_CSS,
            " ".join(page.stylesheets),
            "preview.html is missing the shared stylesheet " + THEMES_CSS,
        )
        self.assertIn(
            THEME_JS,
            " ".join(page.scripts),
            "preview.html is missing the shared script " + THEME_JS,
        )

    def test_index_keeps_the_same_order(self):
        """The reference template must keep the order preview.html now matches."""
        page = _parse(INDEX)
        themes = _index_of(page.stylesheets, THEMES_CSS)
        style = _index_of(page.stylesheets, STYLE_CSS)
        self.assertNotEqual(themes, -1, "index.html does not load " + THEMES_CSS)
        self.assertNotEqual(style, -1, "index.html does not load " + STYLE_CSS)
        self.assertLess(themes, style)


if __name__ == "__main__":
    unittest.main()
