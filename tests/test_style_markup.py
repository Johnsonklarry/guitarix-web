#!/usr/bin/env python3
"""
Test markup hooks in templates.
"""
import unittest
from html.parser import HTMLParser
from pathlib import Path


REQUIRED_HOOKS = ("header", "transport-controls", "status", "broadcast-status")
TEMPLATE_FILES = [
    Path("templates/index.html"),
    Path("preview.html"),
    Path("templates/broadcast.html"),
]


class HookParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hooks = {hook: [] for hook in REQUIRED_HOOKS}

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        hook_name = attrs_dict.get("data-hook")
        if hook_name in REQUIRED_HOOKS:
            self.hooks[hook_name].append({
                "tag": tag,
                "attrs": attrs_dict,
                "line": self.getpos()[0],
            })


class MarkupHooksTest(unittest.TestCase):
    def test_required_hooks_present_once(self):
        for template_path in TEMPLATE_FILES:
            with self.subTest(template=str(template_path)):
                self.assertTrue(template_path.exists(), f"Template not found: {template_path}")
                content = template_path.read_text(encoding="utf-8")
                parser = HookParser()
                parser.feed(content)

                for hook in REQUIRED_HOOKS:
                    elements = parser.hooks[hook]
                    self.assertEqual(
                        len(elements), 1,
                        f"Hook '{hook}' appears {len(elements)} times in {template_path}, expected exactly once"
                    )
                    # Check no inline style attribute on hooked element
                    elem = elements[0]
                    self.assertNotIn(
                        "style", elem["attrs"],
                        f"Hook '{hook}' element has inline style attribute in {template_path}"
                    )

    def test_no_inline_styles_on_hooked_elements(self):
        """Ensure hooked elements don't have any inline style attributes."""
        for template_path in TEMPLATE_FILES:
            with self.subTest(template=str(template_path)):
                self.assertTrue(template_path.exists(), f"Template not found: {template_path}")
                content = template_path.read_text(encoding="utf-8")
                parser = HookParser()
                parser.feed(content)

                for hook in REQUIRED_HOOKS:
                    elements = parser.hooks[hook]
                    self.assertEqual(len(elements), 1)
                    elem = elements[0]
                    self.assertNotIn("style", elem["attrs"])


if __name__ == "__main__":
    unittest.main()
