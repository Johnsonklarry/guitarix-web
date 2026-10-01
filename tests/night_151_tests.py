#!/usr/bin/env python3
import unittest
import importlib
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


class TestSharedStateReset(unittest.TestCase):
    def test_shared_state_reset_defined_and_clears(self):
        day_gx = importlib.import_module("day_gx_43_tests")
        self.assertTrue(hasattr(day_gx, "shared_state"), "shared_state dict not defined")
        self.assertTrue(hasattr(day_gx, "reset_shared_state"), "reset_shared_state function not defined")
        day_gx.shared_state["leak_key"] = "leak_value"
        day_gx.reset_shared_state()
        self.assertEqual(day_gx.shared_state, {}, "reset_shared_state did not clear shared_state")


if __name__ == "__main__":
    unittest.main()
