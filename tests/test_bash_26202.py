"""Markup hooks for the preview template's header and transport regions.

Issue #26252. (This file is named after the pipeline task that produced it,
not after the issue; the issue is the one named above.) Parses
templates/preview.html with html.parser only -- no DOM, no server, no network,
no audio hardware, no credentials -- and asserts the stable hooks (element ids
and data-* attributes) for the header, transport controls, status and
broadcast-status regions each appear exactly once, and that the elements
carrying those hooks take their layout from the stylesheet rather than from an
inline style attribute.
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
    """Collects ids, data-region values and inline style attributes.

    Every start tag is kept whole as well, so a test can ask what the element
    behind a particular hook looks like -- which attributes it carries --
    instead of only counting attribute values across the page.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.ids = []
        self.data_regions = []
        self.style_attrs = []

    def handle_starttag(self, tag, attrs):
        self._record(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._record(tag, attrs)

    def _record(self, tag, attrs):
        self.tags.append({"tag": tag, "attrs": dict(attrs)})
        for name, value in attrs:
            if name == "id":
                self.ids.append(value)
            elif name == "data-region":
                self.data_regions.append(value)
            elif name == "style":
                self.style_attrs.append(value)

    def by_id(self, element_id):
        """Every start tag whose id is element_id."""
        return [tag for tag in self.tags if tag["attrs"].get("id") == element_id]


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

    def test_hooked_elements_carry_no_inline_style(self):
        # Each hook sits on the element that owns its region's layout, and
        # those elements are styled from the stylesheet. A style attribute on
        # one of them would mean the region had started laying itself out
        # inline -- and the hook would no longer be pointing at the element
        # the CSS styles, which is the whole point of the hook.
        for region, (element_id, data_region) in REQUIRED_HOOKS.items():
            with self.subTest(region=region):
                matches = self.parser.by_id(element_id)
                self.assertEqual(
                    len(matches),
                    1,
                    "expected exactly one element with id %r" % element_id,
                )
                attrs = matches[0]["attrs"]
                self.assertNotIn(
                    "style",
                    attrs,
                    "the %r hook must take its layout from the stylesheet, "
                    "not from an inline style attribute" % region,
                )
                self.assertEqual(
                    attrs.get("data-region"),
                    data_region,
                    "id %r must also carry data-region=%r" % (element_id, data_region),
                )
                self.assertTrue(
                    attrs.get("class"),
                    "the %r hook must keep the class its stylesheet targets" % region,
                )


if __name__ == "__main__":
    unittest.main()
