import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK_RE = re.compile(r'<link\b[^>]*rel="stylesheet"[^>]*href="([^"]*)"[^>]*>')


def sheet_hrefs(name):
    text = (ROOT / "templates" / name).read_text(encoding="utf-8")
    return LINK_RE.findall(text)


def position(hrefs, needle):
    for i, h in enumerate(hrefs):
        if needle in h:
            return i
    return -1


class StylesheetOrderTest(unittest.TestCase):
    def check_order(self, name):
        path = ROOT / "templates" / name
        if not path.exists():
            self.skipTest(name + " not present")
        hrefs = sheet_hrefs(name)
        style = position(hrefs, "'style.css'")
        self.assertGreaterEqual(style, 0, name + " must link style.css")
        broadcast = position(hrefs, "'broadcast.css'")
        if broadcast >= 0:
            self.assertLess(style, broadcast,
                            name + ": style.css must come before broadcast.css")

    def test_index(self):
        self.check_order("index.html")

    def test_preview(self):
        self.check_order("preview.html")

    def test_broadcast(self):
        self.check_order("broadcast.html")
        hrefs = sheet_hrefs("broadcast.html")
        self.assertGreaterEqual(position(hrefs, "'broadcast.css'"), 0)


if __name__ == "__main__":
    unittest.main()
