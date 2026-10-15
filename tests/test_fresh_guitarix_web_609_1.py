import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "forge_core" / "static"


def load_static(relative_path):
    # Resolve under the static root, as a static server would.
    target = (STATIC_DIR / relative_path).resolve()
    if STATIC_DIR.resolve() not in target.parents:
        raise ValueError("path escapes static root")
    return target.read_text(encoding="utf-8")


class OfflinePageRetryTest(unittest.TestCase):
    def setUp(self):
        self.html = load_static("theme-kit/pwa/offline.html")

    def test_still_offline_status_element(self):
        self.assertIn('id="offline-status"', self.html)
        self.assertIn('role="status"', self.html)
        self.assertIn("Still offline", self.html)
        self.assertIn("reconnecting", self.html)

    def test_online_and_probe_wiring(self):
        self.assertIn('window.addEventListener("online", probe)', self.html)
        self.assertIn("fetch(PROBE_URL", self.html)
        self.assertIn("window.location.reload()", self.html)

    def test_retry_triggers_probe_not_bare_navigation(self):
        self.assertIn('retry.addEventListener("click"', self.html)
        self.assertIn("event.preventDefault();", self.html)
        click = self.html.split('retry.addEventListener("click"', 1)[1]
        handler = click.split("});", 1)[0]
        self.assertIn("probe();", handler)
        self.assertNotIn("location.href", handler)
        self.assertNotIn("location.assign", handler)

    def test_register_refreshes_on_online(self):
        js = load_static("theme-kit/pwa/register.js")
        self.assertIn('addEventListener("online"', js)
        self.assertIn("registration.update()", js)


if __name__ == "__main__":
    unittest.main()
