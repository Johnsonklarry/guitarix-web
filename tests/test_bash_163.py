"""Regression test for issue #163: the existing style guard must pass.

This runs the real tests.test_style_markup.ExistingStyleGuardTest entry
point through unittest's loader against the real stylesheets and asserts
that every check passes.  Offline and read-only.
"""

import unittest

from tests.test_style_markup import ExistingStyleGuardTest


class Bash163RegressionTest(unittest.TestCase):
    def test_existing_style_guard_passes(self):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ExistingStyleGuardTest)
        self.assertGreater(
            suite.countTestCases(),
            0,
            "ExistingStyleGuardTest must expose at least one test",
        )
        result = unittest.TestResult()
        suite.run(result)
        failures = [
            "%s: %s" % (test, detail)
            for test, detail in list(result.failures) + list(result.errors)
        ]
        self.assertEqual(
            [],
            failures,
            "ExistingStyleGuardTest failed against the real stylesheets:\n"
            + "\n".join(failures),
        )
        self.assertTrue(result.wasSuccessful())


if __name__ == "__main__":
    unittest.main()
