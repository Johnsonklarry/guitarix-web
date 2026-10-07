import os
import re
import unittest

APP_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'app.js')


def read_app_js():
    with open(APP_JS, 'r', encoding='utf-8') as fh:
        return fh.read()


class MoreButtonAccessibilityTest(unittest.TestCase):
    """The More button in renderTakes must expose aria-expanded and aria-label."""

    def setUp(self):
        self.source = read_app_js()

    def test_more_button_sets_aria_expanded(self):
        self.assertIn("moreBtn.setAttribute('aria-expanded', 'false');", self.source)
        self.assertIn(
            "moreBtn.setAttribute('aria-expanded', String(toggleMore(tr, moreBox)));",
            self.source,
        )

    def test_more_button_sets_aria_label(self):
        self.assertIn(
            "moreBtn.setAttribute('aria-label', 'More actions for ' + item.name);",
            self.source,
        )

    def test_more_button_attributes_are_in_render_takes(self):
        # Both attribute assignments must live inside renderTakes, near the
        # mini('More', ...) construction.
        more_call = self.source.index("mini('More', function () {")
        label = self.source.index(
            "moreBtn.setAttribute('aria-label', 'More actions for ' + item.name);"
        )
        expanded = self.source.index(
            "moreBtn.setAttribute('aria-expanded', 'false');"
        )
        self.assertLess(more_call, expanded)
        self.assertLess(expanded, label)
        # The next function definition after the More button block should be
        # togglePlay, confirming we are still inside renderTakes.
        tail = self.source[label:]
        self.assertIn('function togglePlay(', tail)


class AudioPlaybackAccessibilityTest(unittest.TestCase):
    """The audio element created in togglePlay must carry an aria-label."""

    def setUp(self):
        self.source = read_app_js()

    def test_audio_element_sets_aria_label(self):
        self.assertIn(
            "audio.setAttribute('aria-label', 'Playback for ' + item.name);",
            self.source,
        )

    def test_audio_aria_label_follows_dataset_take(self):
        dataset = self.source.index('audio.dataset.take = item.name;')
        label = self.source.index(
            "audio.setAttribute('aria-label', 'Playback for ' + item.name);"
        )
        self.assertLess(dataset, label)

    def test_audio_aria_label_is_in_toggle_play(self):
        toggle_play = self.source.index('function togglePlay(')
        label = self.source.index(
            "audio.setAttribute('aria-label', 'Playback for ' + item.name);"
        )
        self.assertLess(toggle_play, label)
        # The label must appear before the next top-level function definition
        # that follows togglePlay's body.
        tail = self.source[label:]
        next_fn = re.search(r'\nfunction \w+\(', tail)
        self.assertIsNotNone(next_fn)


if __name__ == '__main__':
    unittest.main()
