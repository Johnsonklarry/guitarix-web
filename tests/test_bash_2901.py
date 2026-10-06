#!/usr/bin/env python3
"""Regression test for issue #25902.

PRESET_METHODS in gx_rpc.py must hold the confirmed method names from the
probe output (every value a non-empty string, or None for "move"), and
README.md must name the guitarix build the block was verified against.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gx_rpc  # noqa: E402


EXPECTED_KEYS = {
    "save_current",
    "save_as",
    "rename",
    "delete",
    "new_bank",
    "delete_bank",
    "move",
}


class PresetMethodsTest(unittest.TestCase):
    def test_keys_unchanged(self):
        self.assertEqual(set(gx_rpc.PRESET_METHODS), EXPECTED_KEYS)

    def test_values_are_non_empty_strings_or_none(self):
        for key, value in gx_rpc.PRESET_METHODS.items():
            if value is None:
                # only "move" is allowed to be absent
                self.assertEqual(key, "move",
                                 "%s must not be None" % key)
                continue
            self.assertIsInstance(value, str,
                                  "%s must be a string, got %r" % (key, value))
            self.assertTrue(value.strip(),
                            "%s must be a non-empty string" % key)

    def test_confirmed_names(self):
        self.assertEqual(gx_rpc.PRESET_METHODS["save_current"],
                         "save_current_preset")
        self.assertEqual(gx_rpc.PRESET_METHODS["save_as"], "save_preset")
        self.assertEqual(gx_rpc.PRESET_METHODS["rename"], "rename_preset")
        self.assertEqual(gx_rpc.PRESET_METHODS["delete"], "erase_preset")
        self.assertEqual(gx_rpc.PRESET_METHODS["new_bank"], "bank_insert_new")
        self.assertEqual(gx_rpc.PRESET_METHODS["delete_bank"], "remove_bank")
        self.assertIsNone(gx_rpc.PRESET_METHODS["move"])

    def test_verified_build_constant(self):
        self.assertTrue(hasattr(gx_rpc, "VERIFIED_BUILD"))
        self.assertIn("guitarix", gx_rpc.VERIFIED_BUILD)
        self.assertRegex(gx_rpc.VERIFIED_BUILD, r"\d+\.\d+")


class ReadmeTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            self.text = fh.read()

    def test_readme_names_verified_build(self):
        self.assertIn("guitarix 0.44.1", self.text)

    def test_readme_mentions_probe(self):
        self.assertIn("probe_rpc.py", self.text)

    def test_readme_verified_line_is_near_preset_methods(self):
        idx = self.text.find("PRESET_METHODS")
        self.assertNotEqual(idx, -1, "README must mention PRESET_METHODS")
        window = self.text[idx:idx + 1200]
        self.assertIn("guitarix 0.44.1", window,
                      "the verified-build line must sit in the RPC section")


if __name__ == "__main__":
    unittest.main()
