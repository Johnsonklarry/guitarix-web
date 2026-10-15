import json
import shutil
import subprocess
import unittest
from pathlib import Path


TV_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "forge_core"
    / "static"
    / "theme-kit"
    / "tv.js"
)


class PanicChordTest(unittest.TestCase):
    def run_tv(self, release_at=None):
        if shutil.which("node") is None:
            self.skipTest("Node.js is required for the JavaScript harness")

        harness = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[1], "utf8");
const releaseAt = JSON.parse(process.argv[2]);
let now = 0;
let nextId = 1;
const timers = new Map();
const events = [];
const actions = [];
let triggerChange;

const root = {
  attributes: {},
  getAttribute(name) { return this.attributes[name] || null; },
  setAttribute(name, value) { this.attributes[name] = value; },
  removeAttribute(name) { delete this.attributes[name]; }
};
const document = {
  documentElement: root,
  body: {},
  activeElement: null,
  addEventListener() {},
  dispatchEvent(event) { events.push(event.type); },
  querySelector() { return null; }
};
const window = {
  document,
  ThemeKit: {},
  GamepadNav: {
    onTriggers(callback) {
      triggerChange = callback;
      callback(false, false);
      return () => {};
    }
  },
  location: { search: "?tv=1" },
  URLSearchParams,
  CustomEvent: class {
    constructor(type, options) {
      this.type = type;
      this.bubbles = options.bubbles;
    }
  },
  localStorage: { getItem() { return null; }, setItem() {} },
  addEventListener() {},
  setTimeout(callback, delay) {
    const id = nextId++;
    timers.set(id, { at: now + delay, callback });
    return id;
  },
  clearTimeout(id) { timers.delete(id); },
  requestAnimationFrame() { return 0; },
  cancelAnimationFrame() {}
};

function advance(to) {
  while (true) {
    let firstId = null;
    let firstAt = Infinity;
    for (const [id, timer] of timers) {
      if (timer.at <= to && timer.at < firstAt) {
        firstId = id;
        firstAt = timer.at;
      }
    }
    if (firstId === null) break;
    now = firstAt;
    const timer = timers.get(firstId);
    timers.delete(firstId);
    timer.callback();
  }
  now = to;
}

vm.runInNewContext(source, { window });
window.ThemeKit.tv.bypassAll = () => actions.push("bypass");
window.ThemeKit.tv.muteOutput = () => actions.push("mute");
triggerChange(true, true);
if (releaseAt !== null) {
  advance(releaseAt);
  triggerChange(false, true);
}
advance(1000);
console.log(JSON.stringify({ events, actions }));
"""
        result = subprocess.run(
            ["node", "-e", harness, str(TV_SCRIPT), json.dumps(release_at)],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def test_hold_fires_once(self):
        self.assertEqual(
            self.run_tv(),
            {"events": ["tv:panic"], "actions": ["bypass", "mute"]},
        )

    def test_release_before_deadline_cancels(self):
        self.assertEqual(
            self.run_tv(400),
            {"events": [], "actions": []},
        )


if __name__ == "__main__":
    unittest.main()
