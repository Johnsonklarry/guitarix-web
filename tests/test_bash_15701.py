import os
import re
import unittest

TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates",
    "index.html",
)


class LiveButtonAccessibleNameTest(unittest.TestCase):
    def setUp(self):
        with open(TEMPLATE, encoding="utf-8") as fh:
            self.html = fh.read()

    def test_live_button_has_aria_label(self):
        match = re.search(
            r'<button[^>]*id="btn-live"[^>]*>',
            self.html,
        )
        self.assertIsNotNone(match, "btn-live button not found")
        tag = match.group(0)
        self.assertIn('aria-label="Live mode"', tag)

    def test_live_button_keeps_existing_attributes(self):
        match = re.search(
            r'<button[^>]*id="btn-live"[^>]*>',
            self.html,
        )
        self.assertIsNotNone(match, "btn-live button not found")
        tag = match.group(0)
        self.assertIn('class="listen live-enter"', tag)
        self.assertIn('type="button"', tag)
        self.assertIn(
            'title="Big buttons for playing: presets, record, loop, backing"',
            tag,
        )

    def test_live_button_has_visible_label_text(self):
        match = re.search(
            r'<button[^>]*id="btn-live"[^>]*>(.*?)</button>',
            self.html,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "btn-live button contents not found")
        self.assertIn("Live", match.group(1))


class FooterDisclaimerTest(unittest.TestCase):
    def setUp(self):
        with open(TEMPLATE, encoding="utf-8") as fh:
            self.html = fh.read()

    def test_old_disclaimer_removed(self):
        self.assertNotIn(
            "Any resemblance to actual websites, living or dead, "
            "is purely coincidental",
            self.html,
        )

    def test_new_footer_message_present(self):
        self.assertIn(
            "Made for playing guitar. Your rig, your takes, your browser.",
            self.html,
        )

    def test_footer_message_is_inside_chassis(self):
        match = re.search(
            r'<footer class="chassis">(.*?)</footer>',
            self.html,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "chassis footer not found")
        self.assertIn(
            "Made for playing guitar. Your rig, your takes, your browser.",
            match.group(1),
        )


if __name__ == "__main__":
    unittest.main()
