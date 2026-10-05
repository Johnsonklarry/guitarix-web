"""Regression tests for issue #165: shared style layers in templates/index.html.

The page must load the shared style layers (tokens, base, cards, tables,
badges, collapsible) before its own stylesheet, so the page stylesheet can
still override them, and the migration must not drop the element ids that
static/app.js binds to.
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(REPO_ROOT, "templates", "index.html")

SHARED_LAYERS = (
    "style/tokens.css",
    "style/base.css",
    "style/cards.css",
    "style/tables.css",
    "style/badges.css",
    "style/collapsible.css",
)


class SharedStyleLayersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(INDEX_PATH, encoding="utf-8") as handle:
            cls.html = handle.read()

    def test_shared_layers_load_before_page_stylesheet(self):
        tokens_at = self.html.find("style/tokens.css")
        page_at = self.html.find("style.css")
        self.assertNotEqual(tokens_at, -1, "style/tokens.css link is missing")
        self.assertNotEqual(page_at, -1, "style.css link is missing")
        self.assertLess(
            tokens_at,
            page_at,
            "shared style layers must load before the page stylesheet",
        )

    def test_every_shared_layer_is_linked(self):
        for layer in SHARED_LAYERS:
            with self.subTest(layer=layer):
                self.assertIn(layer, self.html)

    def test_app_js_hooks_survive(self):
        self.assertIn('id="takes"', self.html)
        self.assertIn('id="presets"', self.html)


if __name__ == "__main__":
    unittest.main()
