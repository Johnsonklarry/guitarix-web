#!/usr/bin/env python3
"""
Night 64 tests: fakejack.load() rejects a malformed graph.json with a clear
error instead of returning it for callers to trip over.

    python3 tests/night_64_tests.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fakes"))

import fakejack  # noqa: E402


class MalformedGraphState(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gxweb-jack-")
        self._old = os.environ.get("FAKE_JACK_DIR")
        os.environ["FAKE_JACK_DIR"] = self.dir
        self.path = os.path.join(self.dir, "graph.json")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("FAKE_JACK_DIR", None)
        else:
            os.environ["FAKE_JACK_DIR"] = self._old
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, text):
        with open(self.path, "w") as f:
            f.write(text)

    def test_missing_keys_rejected(self):
        self.write(json.dumps({"invalid_key": 1}))
        with self.assertRaises(SystemExit) as cm:
            fakejack.load()
        self.assertIn("malformed", str(cm.exception))

    def test_missing_connections_rejected(self):
        self.write(json.dumps({"ports": {}}))
        with self.assertRaises(SystemExit):
            fakejack.load()

    def test_wrong_types_rejected(self):
        for bad in ([], "x", {"ports": [], "connections": []},
                    {"ports": {}, "connections": {}}):
            self.write(json.dumps(bad))
            with self.assertRaises(SystemExit):
                fakejack.load()

    def test_invalid_json_rejected(self):
        self.write("{not json")
        with self.assertRaises(SystemExit):
            fakejack.load()

    def test_downstream_ops_fail_cleanly(self):
        self.write(json.dumps({"ports": {}}))
        with self.assertRaises(SystemExit):
            fakejack.connections_of("system:capture_1")

    def test_valid_state_and_seed_still_work(self):
        state = fakejack.load()          # no file: seeds the default rig
        self.assertIn("system:capture_1", state["ports"])
        self.assertEqual(fakejack.load(), state)   # reload of a valid file
        self.assertTrue(fakejack.connect("system:capture_2", "gx_head_fx:in_0"))


if __name__ == "__main__":
    unittest.main()
