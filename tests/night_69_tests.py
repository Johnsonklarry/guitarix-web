#!/usr/bin/env python3
"""
Night 69 tests: _extract_json must not join mismatched JSON delimiters.

    python3 tests/night_69_tests.py

Text around the JSON that contains a different bracket type used to be pulled
into the span (earliest { or [ to latest } or ]), giving invalid JSON like
"{...] ". No guitarix and no JACK are needed.
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import presets_io  # noqa: E402


class ExtractJsonTests(unittest.TestCase):
    def test_object_then_trailing_bracket(self):
        text = 'Here you go: {"name": "A", "params": {}} (see note] ok'
        out = presets_io._extract_json(text)
        self.assertEqual(json.loads(out), {"name": "A", "params": {}})

    def test_stray_bracket_before_object(self):
        text = 'Use [this] preset: {"name": "A", "params": {}} thanks'
        out = presets_io._extract_json(text)
        # The first balanced value wins; it must at least be valid JSON.
        json.loads(out)

    def test_array_with_trailing_brace(self):
        text = 'Presets: [{"name": "A", "params": {}}] and a } later'
        out = presets_io._extract_json(text)
        self.assertEqual(json.loads(out), [{"name": "A", "params": {}}])

    def test_parse_survives_surrounding_text(self):
        text = 'Sure! {"name": "A", "params": {"amp.fuzz": 0.05}} hope that helps ]'
        result = presets_io.parse(text)
        self.assertEqual(result["presets"][0]["name"], "A")

    def test_fenced_block_unchanged(self):
        text = 'x\n```json\n{"a": 1}\n```\ny'
        self.assertEqual(json.loads(presets_io._extract_json(text)), {"a": 1})

    def test_no_json_returned_as_is(self):
        self.assertEqual(presets_io._extract_json("  no json here "), "no json here")

    def test_unbalanced_falls_back_to_span(self):
        out = presets_io._extract_json('pre {"a": 1, ] post')
        self.assertTrue(out.startswith("{"))


if __name__ == "__main__":
    unittest.main()
