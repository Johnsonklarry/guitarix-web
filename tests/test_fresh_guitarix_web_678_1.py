import shutil
import subprocess
import unittest
from pathlib import Path


class FreshGuitarixWeb6781Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        cls.gamepad_js = Path(__file__).resolve().parents[1] / "forge_core" / "static" / "gamepad.js"

    def run_node(self, script):
        if not self.node:
            self.skipTest("Node.js is required for the JavaScript DOM stub test")
        subprocess.run(
            [self.node, "-e", script, str(self.gamepad_js)],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_input_starts_polling_until_a_pad_is_found(self):
        self.run_node(
            r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const sourcePath = process.argv[1];

const listeners = Object.create(null);
const timers = [];
let nextTimerId = 1;
const document = {
  currentScript: null,
  addEventListener(type, callback) {
    (listeners[type] || (listeners[type] = [])).push(callback);
  },
  removeEventListener(type, callback) {
    listeners[type] = (listeners[type] || []).filter((item) => item !== callback);
  }
};
function dispatch(type) {
  (listeners[type] || []).slice().forEach((callback) => callback({ type }));
}
const navigator = {
  calls: 0,
  getGamepads() {
    this.calls++;
    return this.calls >= 3 ? [{ id: "fake pad" }] : [null];
  }
};
const window = {
  document,
  navigator,
  setTimeout(callback, delay) {
    const timer = { id: nextTimerId++, callback, delay, cancelled: false };
    timers.push(timer);
    return timer.id;
  },
  clearTimeout(id) {
    const timer = timers.find((item) => item.id === id);
    if (timer) timer.cancelled = true;
  },
  GamepadNav: {
    started: false,
    starts: 0,
    configure() {},
    isStarted() { return this.started; },
    start() { this.starts++; this.started = true; },
    stop() { this.started = false; },
    handleAction() { return true; },
    moveFocus() { return true; }
  }
};
global.window = window;
vm.runInThisContext(fs.readFileSync(sourcePath, "utf8"), { filename: sourcePath });

window.ForgeGamepad.enable();
assert.strictEqual(window.GamepadNav.starts, 1);
assert.strictEqual(navigator.calls, 0);
dispatch("keydown");
assert.strictEqual(navigator.calls, 1);

while (timers.length) {
  const timer = timers.shift();
  if (!timer.cancelled) timer.callback();
}
assert.strictEqual(navigator.calls, 3);
assert.strictEqual(window.GamepadNav.starts, 1);
assert.strictEqual((listeners.keydown || []).length, 0);
assert.strictEqual((listeners.pointerdown || []).length, 0);

dispatch("keydown");
dispatch("pointerdown");
assert.strictEqual(navigator.calls, 3);
assert.strictEqual(window.GamepadNav.starts, 1);
"""
        )

    def test_disable_removes_input_listeners_before_polling(self):
        self.run_node(
            r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const sourcePath = process.argv[1];

const listeners = Object.create(null);
let timerCount = 0;
const document = {
  currentScript: null,
  addEventListener(type, callback) {
    (listeners[type] || (listeners[type] = [])).push(callback);
  },
  removeEventListener(type, callback) {
    listeners[type] = (listeners[type] || []).filter((item) => item !== callback);
  }
};
function dispatch(type) {
  (listeners[type] || []).slice().forEach((callback) => callback({ type }));
}
const navigator = {
  calls: 0,
  getGamepads() {
    this.calls++;
    return [null];
  }
};
const window = {
  document,
  navigator,
  setTimeout() { timerCount++; return timerCount; },
  clearTimeout() {},
  GamepadNav: {
    started: false,
    starts: 0,
    configure() {},
    isStarted() { return this.started; },
    start() { this.starts++; this.started = true; },
    stop() { this.started = false; },
    handleAction() { return true; },
    moveFocus() { return true; }
  }
};
global.window = window;
vm.runInThisContext(fs.readFileSync(sourcePath, "utf8"), { filename: sourcePath });

window.ForgeGamepad.enable();
const startsBeforeDisable = window.GamepadNav.starts;
window.ForgeGamepad.disable();

assert.strictEqual((listeners.keydown || []).length, 0);
assert.strictEqual((listeners.pointerdown || []).length, 0);
dispatch("keydown");
dispatch("pointerdown");
assert.strictEqual(navigator.calls, 0);
assert.strictEqual(timerCount, 0);
assert.strictEqual(window.GamepadNav.starts, startsBeforeDisable);
"""
        )


if __name__ == "__main__":
    unittest.main()
