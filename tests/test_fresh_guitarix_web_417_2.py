"""Tests for the Joplin "Retry" affordance (issue #417).

These tests exercise ``render_view`` only: no network access and no Joplin
client are involved.  ``guitarix-web`` is loaded directly from its path
because the directory name is not a valid Python package identifier.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_MODULE_NAME = "joplin_under_test_417"
_MODULE_PATH = Path(__file__).resolve().parents[1] / "guitarix-web" / "joplin.py"

_spec = importlib.util.spec_from_file_location(_MODULE_NAME, _MODULE_PATH)
joplin = importlib.util.module_from_spec(_spec)
sys.modules[_MODULE_NAME] = joplin
_spec.loader.exec_module(joplin)

JoplinView = joplin.JoplinView
render_view = joplin.render_view

RETRY_HOOK = 'data-joplin-action="retry"'


class RetryAffordanceTests(unittest.TestCase):
    """Issue #417: a retry action when Joplin is unreachable."""

    def test_unavailable_view_offers_retry(self):
        view = JoplinView(available=False, reason="connection refused")
        fragment = render_view(view)
        # The visible label and the machine-readable hook are both present.
        self.assertIn("Retry", fragment)
        self.assertIn(RETRY_HOOK, fragment)
        # The ordinary notice / reason section still renders.
        self.assertIn(joplin.UNAVAILABLE_NOTICE, fragment)
        self.assertIn("connection refused", fragment)

    def test_available_view_has_no_retry(self):
        view = JoplinView(available=True)
        fragment = render_view(view)
        self.assertNotIn("Retry", fragment)
        self.assertNotIn(RETRY_HOOK, fragment)
        self.assertNotIn("retry", fragment)

    def test_available_rendering_is_byte_for_byte_unchanged(self):
        view = JoplinView(available=True)
        self.assertEqual(
            render_view(view),
            '<section class="joplin" data-joplin="available"></section>',
        )


if __name__ == "__main__":
    unittest.main()
