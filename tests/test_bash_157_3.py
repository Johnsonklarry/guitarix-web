"""Regression test for issue #15703: Listen button 44px tap target.

Reads static/broadcast.css and checks that the .listen rule declares a
44px minimum tap target, that the lamp rules survive, and that the
stylesheet import is still the first non-comment line.
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_PATH = os.path.join(REPO_ROOT, "static", "broadcast.css")


def read_css():
    with open(CSS_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def extract_rule(css, selector):
    """Return the body of the first rule whose selector matches exactly."""
    pattern = re.compile(
        r"(?:^|[};])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}",
        re.MULTILINE,
    )
    match = pattern.search(css)
    if match is None:
        raise AssertionError("rule not found: %s" % selector)
    return match.group(1)


def first_non_comment_line(css):
    """Return the first line that is not blank and not inside a comment."""
    in_comment = False
    for raw_line in css.splitlines():
        line = raw_line.strip()
        if in_comment:
            if "*/" in line:
                in_comment = False
                line = line.split("*/", 1)[1].strip()
            else:
                continue
        while line.startswith("/*"):
            if "*/" in line:
                line = line.split("*/", 1)[1].strip()
            else:
                in_comment = True
                line = ""
                break
        if line:
            return line
    return ""


class ListenTapTargetTest(unittest.TestCase):
    def setUp(self):
        self.css = read_css()

    def test_listen_rule_has_min_height(self):
        body = extract_rule(self.css, ".listen")
        self.assertIn("min-height: 44px", body)

    def test_listen_rule_has_min_width(self):
        body = extract_rule(self.css, ".listen")
        self.assertIn("min-width: 44px", body)

    def test_listen_lamp_rule_still_present(self):
        body = extract_rule(self.css, ".listen__lamp")
        self.assertIn("border-radius", body)

    def test_listen_on_lamp_rule_still_present(self):
        body = extract_rule(self.css, ".listen.is-on .listen__lamp")
        self.assertIn("background", body)

    def test_import_is_first_non_comment_line(self):
        self.assertEqual(
            first_non_comment_line(self.css),
            '@import url("./style.css");',
        )


if __name__ == "__main__":
    unittest.main()
