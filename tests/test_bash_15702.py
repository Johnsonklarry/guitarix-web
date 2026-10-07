import os
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(REPO_ROOT, "templates", "index.html")


class TestLiveKeysHintWording(unittest.TestCase):
    def setUp(self):
        with open(INDEX_HTML, encoding="utf-8") as fh:
            self.html = fh.read()

    def test_live_keys_hint_says_keyboard_shortcuts(self):
        self.assertIn(
            '<p class="live__keys">Keyboard shortcuts: &larr; &rarr; presets '
            '&middot; Space record &middot; B backing &middot; L loop</p>',
            self.html,
        )

    def test_live_keys_hint_no_longer_says_pedal_keys(self):
        self.assertNotIn("Pedal keys:", self.html)


if __name__ == "__main__":
    unittest.main()
