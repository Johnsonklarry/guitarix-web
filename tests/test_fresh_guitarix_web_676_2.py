import shutil
import subprocess
import unittest
from pathlib import Path


COMFORT_JS = Path(__file__).resolve().parents[1] / "forge_core" / "static" / "theme-kit" / "comfort.js"


class ComfortStorageSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if cls.node is None:
            raise unittest.SkipTest("Node.js is required for the jsdom harness")

        result = subprocess.run(
            [cls.node, "-e", "require('jsdom')"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise unittest.SkipTest("jsdom is required for the comfort.js harness")

    def test_storage_event_updates_comfort_without_writing_storage(self):
        script = r"""
const assert = require('assert');
const fs = require('fs');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'https://example.test/',
  runScripts: 'outside-only'
});
const window = dom.window;
window.eval(fs.readFileSync(process.argv[1], 'utf8'));

let comfortEventFired = false;
window.document.addEventListener('themekit:comfort', function () {
  comfortEventFired = true;
});

const storagePrototype = Object.getPrototypeOf(window.localStorage);
const originalSetItem = storagePrototype.setItem;
let setItemCalls = 0;
storagePrototype.setItem = function () {
  setItemCalls += 1;
  return originalSetItem.apply(this, arguments);
};

window.dispatchEvent(new window.StorageEvent('storage', {
  key: 'theme-kit-comfort',
  newValue: '{"level":"large","contrast":true,"motion":false}',
  storageArea: window.localStorage
}));

assert.strictEqual(window.document.documentElement.getAttribute('data-comfort'), 'large');
assert.strictEqual(window.document.documentElement.getAttribute('data-comfort-contrast'), 'on');
assert.strictEqual(window.document.documentElement.getAttribute('data-comfort-motion'), 'off');
assert.strictEqual(comfortEventFired, true);
assert.strictEqual(setItemCalls, 0);

dom.window.close();
"""
        result = subprocess.run(
            [self.node, "-e", script, str(COMFORT_JS)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
