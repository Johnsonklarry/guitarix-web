import unittest
from pathlib import Path

class TestSocketIOAsset(unittest.TestCase):
    def setUp(self):
        self.template_path = Path(__file__).resolve().parent.parent / "templates" / "index.html"
        self.content = self.template_path.read_text(encoding="utf-8")

    def test_references_socketio_min_js_via_asset(self):
        self.assertIn("{{ asset('socket.io.min.js') }}", self.content)

    def test_no_cdn_url_remains(self):
        self.assertNotIn("cdn.socket.io", self.content)

if __name__ == "__main__":
    unittest.main()
