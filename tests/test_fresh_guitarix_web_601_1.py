import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BroadcastPilotClassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "static" / "broadcast.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "static" / "broadcast.css").read_text(encoding="utf-8")

    def test_every_toggled_pilot_class_has_a_css_rule(self):
        toggled = set(
            re.findall(
                r"E\.pilot\.classList\.toggle\(['\"]([^'\"]+)['\"]",
                self.js,
            )
        )
        self.assertTrue(toggled)
        for name in toggled:
            with self.subTest(name=name):
                self.assertRegex(
                    self.css,
                    r"\.pilot\." + re.escape(name) + r"(?=[\s:{,.#\[])",
                )

    def test_live_class_matches_stylesheet(self):
        self.assertRegex(
            self.js,
            r"E\.pilot\.classList\.toggle\(['\"]is-live['\"],\s*linkUp\)",
        )
        self.assertRegex(self.css, r"\.pilot\.is-live\s*\{")
        self.assertNotRegex(
            self.js, r"E\.pilot\.classList\.toggle\(['\"]is-on['\"]"
        )

    def test_disconnected_snapshot_sets_fault_instead_of_live(self):
        snapshot = re.search(
            r"socket\.on\(['\"]snapshot['\"],\s*function\s*\(d\)\s*\{"
            r"(?P<body>.*?)\n  \}\);",
            self.js,
            re.DOTALL,
        )
        self.assertIsNotNone(snapshot)
        self.assertRegex(
            snapshot.group("body"),
            r"setLink\(d\.connected,\s*d\.connected\s*===\s*false\)",
        )
        self.assertRegex(
            self.js,
            r"E\.pilot\.classList\.toggle\(['\"]is-fault['\"],\s*!!fault\s*&&\s*!linkUp\)",
        )
        self.assertRegex(self.css, r"\.pilot\.is-fault\s*\{")


if __name__ == "__main__":
    unittest.main()
