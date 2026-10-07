"""Regression test for issue #2604: SooperLooper feasibility findings."""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_PATH = os.path.join(REPO_ROOT, "docs", "sooperlooper_spike.md")

REQUIRED_HEADINGS = (
    "## Install result",
    "## OSC control result",
    "## Measured CPU/memory cost",
)


class SooperLooperSpikeDocTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            os.path.isfile(DOC_PATH),
            "docs/sooperlooper_spike.md must exist",
        )
        with open(DOC_PATH, encoding="utf-8") as handle:
            self.text = handle.read()

    def test_has_go_no_go_line(self):
        lowered = self.text.lower()
        self.assertTrue(
            "go/no-go" in lowered,
            "document must contain an explicit go/no-go line",
        )
        self.assertTrue(
            "go -" in lowered or "no-go -" in lowered,
            "document must state an explicit go or no-go decision",
        )

    def test_has_three_findings_headings(self):
        for heading in REQUIRED_HEADINGS:
            self.assertIn(
                heading,
                self.text,
                "document must contain the findings heading %r" % heading,
            )


if __name__ == "__main__":
    unittest.main()
