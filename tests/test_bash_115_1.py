import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO_ROOT, "templates", "broadcast.html")


class BroadcastMarkupContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(TEMPLATE, encoding="utf-8") as handle:
            cls.html = handle.read()

    def test_required_ids_present(self):
        for element_id in (
            "pilot",
            "status",
            "bank",
            "preset",
            "rolling",
            "takes",
            "stream",
            "transport-controls",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn('id="%s"' % element_id, self.html)

    def test_data_regions_present(self):
        for region in ("status", "header", "broadcast-status", "transport-controls"):
            with self.subTest(region=region):
                self.assertIn('data-region="%s"' % region, self.html)

    def test_asset_order(self):
        themes = self.html.index("theme-kit/themes.css")
        style = self.html.index("style.css")
        broadcast = self.html.index("broadcast.css")
        script = self.html.index("broadcast.js")
        self.assertLess(themes, style)
        self.assertLess(style, broadcast)
        self.assertLess(broadcast, script)

    def test_audio_has_accessible_name_and_controls(self):
        start = self.html.index('id="stream"')
        end = self.html.index(">", start)
        tag = self.html[start:end]
        self.assertIn('aria-label="Live stream"', tag)
        self.assertIn("controls", tag)


if __name__ == "__main__":
    unittest.main()
