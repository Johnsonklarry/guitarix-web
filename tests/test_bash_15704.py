import os
import re
import unittest

CSS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "static",
    "broadcast.css",
)


def _read_css():
    with open(CSS_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def _rule_body(css, selector):
    """Return the declaration block for the first rule whose selector list
    contains `selector` as a whole selector."""
    pattern = re.compile(r"([^{}]+)\{([^{}]*)\}")
    for match in pattern.finditer(css):
        selectors = [s.strip() for s in match.group(1).split(",")]
        if selector in selectors:
            return match.group(2)
    return None


class ListenTapTargetTest(unittest.TestCase):
    def setUp(self):
        self.css = _read_css()

    def test_listen_rule_exists(self):
        body = _rule_body(self.css, ".listen")
        self.assertIsNotNone(body, ".listen rule not found in broadcast.css")

    def test_listen_has_min_height_44px(self):
        body = _rule_body(self.css, ".listen")
        self.assertIsNotNone(body)
        match = re.search(r"min-height\s*:\s*([^;]+);", body)
        self.assertIsNotNone(match, "min-height not declared on .listen")
        self.assertEqual(match.group(1).strip(), "44px")

    def test_listen_has_min_width_44px(self):
        body = _rule_body(self.css, ".listen")
        self.assertIsNotNone(body)
        match = re.search(r"min-width\s*:\s*([^;]+);", body)
        self.assertIsNotNone(match, "min-width not declared on .listen")
        self.assertEqual(match.group(1).strip(), "44px")

    def test_listen_keeps_existing_padding(self):
        body = _rule_body(self.css, ".listen")
        self.assertIsNotNone(body)
        match = re.search(r"padding\s*:\s*([^;]+);", body)
        self.assertIsNotNone(match, "padding not declared on .listen")
        self.assertEqual(match.group(1).strip(), "8px 15px 8px 12px")


if __name__ == "__main__":
    unittest.main()
