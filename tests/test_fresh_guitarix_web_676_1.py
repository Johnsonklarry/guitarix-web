import pathlib
import shutil
import subprocess
import unittest


SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "forge_core/static/theme-kit/comfort.js"
)


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the VM harness")
class ComfortOSHintsTest(unittest.TestCase):
    def test_reduced_motion_hint_and_stored_override(self):
        harness = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(process.argv[1], "utf8");
const values = new Map();

function load(fontSize) {
  const attributes = new Map();
  const root = {
    setAttribute(name, value) { attributes.set(name, value); },
    getAttribute(name) { return attributes.get(name) || null; }
  };
  const document = {
    documentElement: root,
    dispatchEvent() {}
  };
  const window = {
    localStorage: {
      getItem(key) { return values.has(key) ? values.get(key) : null; },
      setItem(key, value) { values.set(key, value); }
    },
    matchMedia(query) {
      assert.equal(query, "(prefers-reduced-motion: reduce)");
      return { matches: true };
    },
    getComputedStyle() { return { fontSize }; }
  };
  class CustomEvent {
    constructor(type, options) {
      this.type = type;
      this.detail = options.detail;
    }
  }
  vm.runInNewContext(source, { window, document, CustomEvent });
  return root;
}

values.clear();
let root = load("20px");
assert.equal(root.getAttribute("data-comfort-motion"), "off");
assert.equal(root.getAttribute("data-comfort"), "large");

values.set("theme-kit-comfort", JSON.stringify({
  level: "standard", contrast: false, motion: true
}));
root = load("20px");
assert.equal(root.getAttribute("data-comfort-motion"), "on");
assert.equal(root.getAttribute("data-comfort"), "standard");
"""
        subprocess.run(
            ["node", "-e", harness, str(SCRIPT)],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
