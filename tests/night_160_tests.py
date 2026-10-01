#!/usr/bin/env python3
"""Offline markup-hook checks for the templates (html.parser only)."""

import os
import unittest
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED = {
    "index.html": ["header", "status", "transport-controls"],
    "broadcast.html": ["header", "status", "broadcast-status", "transport-controls"],
}
IDS = {
    "header": "app-header",
    "status": "app-status",
    "transport-controls": "transport-controls",
    "broadcast-status": "rolling",
}


class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def parse(name):
    with open(os.path.join(ROOT, "templates", name), encoding="utf-8") as f:
        c = Collector()
        c.feed(f.read())
    return c.elements


class MarkupHooksTest(unittest.TestCase):
    def test_hooks_appear_exactly_once(self):
        for name, regions in REQUIRED.items():
            els = parse(name)
            for region in regions:
                with self.subTest(template=name, region=region):
                    by_data = [e for e in els if e[1].get("data-region") == region]
                    self.assertEqual(len(by_data), 1, "data-region=%s" % region)
                    hook_id = IDS[region]
                    by_id = [e for e in els if e[1].get("id") == hook_id]
                    self.assertEqual(len(by_id), 1, "id=%s" % hook_id)
                    self.assertIs(by_id[0], by_data[0])

    def test_no_inline_style_on_hooks(self):
        for name in REQUIRED:
            for tag, attrs in parse(name):
                if "data-region" in attrs:
                    with self.subTest(template=name, region=attrs["data-region"]):
                        self.assertNotIn("style", attrs)


if __name__ == "__main__":
    unittest.main()
