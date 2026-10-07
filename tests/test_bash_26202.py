"""Markup hooks for the preview template's header and transport regions.

Issue #26252. Parses templates/preview.html with html.parser only -- no DOM,
no server, no network, no audio hardware, no credentials -- and asserts the
stable hooks (element ids and data-* attributes) for the header, transport
controls, status and broadcast-status regions each appear exactly once, and
that no inline style attribute is required for layout.
"""

import os
import unittest
from html.parser import HTMLParser

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
PREVIEW = os.path.join(TEMPLATES_DIR, "preview.html")

# region name -> (required element id, required data-region value)
REQUIRED_HOOKS = {
    "header": ("header", "header"),
    "transport-controls": ("transport-controls", "transport-controls"),
    "status": ("status", "status"),
    "broadcast-status": ("offline", "broadcast-status"),
}


class _HookParser(HTMLParser):
    """Collects ids, data-region values and inline style attributes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.data_regions = []
        self.style_attrs = []

    def handle_starttag(self, tag, attrs):
        self._record(attrs)

    def handle_startendtag(self, tag, attrs):
        self._record(attrs)

    def _record(self, attrs):
        for name, value in attrs:
            if name == "id":
                self.ids.append(value)
            elif name == "data-region":
                self.data_regions.append(value)
            elif name == "style":
                self.style_attrs.append(value)


def parse_preview():
    """Parse templates/preview.html and return the hook parser."""
    with open(PREVIEW, "r", encoding="utf-8") as handle:
        text = handle.read()
    parser = _HookParser()
    parser.feed(text)
    parser.close()
    return parser


class MarkupHooksTest(unittest.TestCase):
    def setUp(self):
        self.parser = parse_preview()

    def test_required_hook_ids_appear_exactly_once(self):
        for region, (element_id, _data_region) in REQUIRED_HOOKS.items():
            with self.subTest(region=region):
                self.assertEqual(
                    self.parser.ids.count(element_id),
                    1,
                    "hook id %r for region %r must appear exactly once" % (element_id, region),
                )

    def test_required_data_region_attributes_appear_exactly_once(self):
        for region, (_element_id, data_region) in REQUIRED_HOOKS.items():
            with self.subTest(region=region):
                self.assertEqual(
                    self.parser.data_regions.count(data_region),
                    1,
                    "data-region %r must appear exactly once" % (data_region,),
                )

    def test_no_inline_style_attribute_required_for_layout(self):
        self.assertEqual(
            self.parser.style_attrs,
            [],
            "preview.html must not rely on inline style attributes for layout",
        )

    def test_entry_point_parsed_page_text_exposes_hooks(self):
        # Assert through the template entry point: the parsed page text itself.
        with open(PREVIEW, "r", encoding="utf-8") as handle:
            page = handle.read()
        for region, (element_id, data_region) in REQUIRED_HOOKS.items():
            with self.subTest(region=region):
                self.assertEqual(page.count('id="%s"' % element_id), 1)
                self.assertEqual(page.count('data-region="%s"' % data_region), 1)


if __name__ == "__main__":
    unittest.main()
