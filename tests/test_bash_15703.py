import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "broadcast.html")


class AudioAriaLabelTest(unittest.TestCase):
    def setUp(self):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as handle:
            self.html = handle.read()

    def test_audio_element_has_aria_label(self):
        match = re.search(r"<audio\b[^>]*\bid=\"stream\"[^>]*>", self.html)
        self.assertIsNotNone(match, "audio element with id=\"stream\" not found")
        tag = match.group(0)
        self.assertIn(
            'aria-label="Live guitar rig monitor stream"',
            tag,
            "audio element is missing the expected aria-label attribute",
        )

    def test_audio_element_keeps_existing_attributes(self):
        match = re.search(r"<audio\b[^>]*\bid=\"stream\"[^>]*>", self.html)
        self.assertIsNotNone(match, "audio element with id=\"stream\" not found")
        tag = match.group(0)
        self.assertIn('preload="none"', tag)
        self.assertIn("controls", tag)
        self.assertIn('src="/monitor.mp3"', tag)


if __name__ == "__main__":
    unittest.main()
