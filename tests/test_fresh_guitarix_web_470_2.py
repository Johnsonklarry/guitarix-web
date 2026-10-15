import re
import unittest
from pathlib import Path


class DesignerCsrfHeadersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(__file__).resolve().parents[1]
            / "forge_core"
            / "static"
            / "designer.js"
        ).read_text(encoding="utf-8")

    def test_mutations_add_csrf_header_from_page_token(self):
        request = self.source.split("async function request(", 1)[1].split(
            "function dirty()", 1
        )[0]
        match = re.search(
            r"if\s*\(method\s*!==\s*['\"]GET['\"]\)\s*\{"
            r"(?P<body>.*?)"
            r"\n\s*\}\s*\n\s*response\s*=\s*await fetch",
            request,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group("body")
        self.assertRegex(body, r"document\.(?:querySelector|cookie)")
        self.assertRegex(
            body, r"headers\s*\[\s*['\"]X-CSRF-Token['\"]\s*\]\s*=\s*token"
        )
        self.assertIn("headers,", request)

    def test_get_headers_remain_unchanged(self):
        request = self.source.split("async function request(", 1)[1].split(
            "function dirty()", 1
        )[0]
        self.assertRegex(
            request,
            r"const headers\s*=\s*body\s*===\s*undefined\s*\?\s*\{\s*\}"
            r"\s*:\s*\{\s*['\"]Content-Type['\"]\s*:\s*"
            r"['\"]application/json['\"]\s*\}",
        )
        self.assertNotRegex(request, r"if\s*\(method\s*===\s*['\"]GET['\"]\)")
        self.assertEqual(request.count("X-CSRF-Token"), 1)


if __name__ == "__main__":
    unittest.main()
