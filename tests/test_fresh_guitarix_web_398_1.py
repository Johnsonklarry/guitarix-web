"""Regression test for issue #398: designer fetch timeout and abort handling.

Loads ``forge_core/static/designer.js`` in a jsdom harness with a stubbed
global ``fetch`` that never resolves on its own (it only rejects once its
``AbortSignal`` fires, mirroring real fetch semantics).  The harness invokes
the module's ``request('presets')`` entry point, advances the faked timers
past the 10s timeout, and asserts that the request rejects with
``'request timed out'`` and that the stub fetch received an ``AbortSignal``
that is indeed aborted.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESIGNER_PATH = os.path.join(ROOT, "forge_core", "static", "designer.js")

NODE_HARNESS = r'''"use strict";
const fs = require("fs");
const { JSDOM } = require("jsdom");

const sourcePath = process.argv[2];
const source = fs.readFileSync(sourcePath, "utf8");
const marker = "action(init);";
const instrumented = source.replace(marker, "window.__designerRequest = request;");
if (instrumented === source) {
  process.stderr.write("could not instrument designer.js: bootstrap marker not found\n");
  process.exit(2);
}

const html = [
  "<!DOCTYPE html><html><head><title>designer-harness</title></head><body>",
  '<div id="designer-grid"></div>',
  '<div id="errors" hidden></div>',
  '<div id="status"></div>',
  '<input id="palette-search">',
  '<div id="palette"></div>',
  '<select id="preset-picker"></select>',
  '<input id="preset-name">',
  '<input id="columns">',
  '<input id="row-height">',
  '<button id="new-preset"></button>',
  '<button id="duplicate-preset"></button>',
  '<button id="save-preset"></button>',
  '<button id="delete-preset"></button>',
  '<button id="set-active"></button>',
  '<button id="preview-toggle"></button>',
  '<div id="preview-panel" hidden></div>',
  '<div id="preview-grid"></div>',
  '<form id="options-form"></form>',
  '<div id="options-empty"></div>',
  '<span id="dashboard-label"></span>',
  "</body></html>"
].join("");

const dom = new JSDOM(html, {
  url: "https://example.test/forge/designer/",
  runScripts: "outside-only"
});
const window = dom.window;

function define(name, value) {
  try {
    Object.defineProperty(window, name, { value: value, configurable: true, writable: true });
  } catch (err) {
    window[name] = value;
  }
}

let sequence = 0;
const timers = new Map();
define("setTimeout", function (fn, delay) {
  const id = ++sequence;
  timers.set(id, { fn: fn, delay: Number(delay) || 0 });
  return id;
});
define("clearTimeout", function (id) {
  timers.delete(id);
});
define("setInterval", window.setTimeout);
define("clearInterval", window.clearTimeout);

function advance(ms) {
  let fired = true;
  while (fired) {
    fired = false;
    const pending = Array.from(timers.entries());
    for (const entry of pending) {
      const id = entry[0];
      const timer = entry[1];
      if (timer.delay <= ms) {
        timers.delete(id);
        fired = true;
        timer.fn();
      }
    }
  }
}

let capturedSignal = null;
let capturedUrl = null;
let fetchCalls = 0;
define("fetch", function (url, options) {
  fetchCalls += 1;
  capturedUrl = String(url);
  capturedSignal = options && options.signal ? options.signal : null;
  return new Promise(function (resolve, reject) {
    if (capturedSignal) {
      capturedSignal.addEventListener("abort", function () {
        const abortError = new Error("The operation was aborted.");
        abortError.name = "AbortError";
        reject(abortError);
      });
    }
  });
});

define("AbortController", AbortController);
if (typeof window.structuredClone !== "function" && typeof structuredClone === "function") {
  define("structuredClone", structuredClone);
}

window.eval(instrumented);

(async function () {
  let outcome;
  const promise = window.__designerRequest("presets");
  advance(10001);
  try {
    await promise;
    outcome = { rejected: false };
  } catch (error) {
    outcome = {
      rejected: true,
      message: String(error && error.message),
      messages: error && error.messages ? error.messages : null,
      fetchCalls: fetchCalls,
      url: capturedUrl,
      signalProvided: !!capturedSignal,
      signalAborted: !!(capturedSignal && capturedSignal.aborted)
    };
  }
  process.stdout.write(JSON.stringify(outcome));
})().catch(function (error) {
  process.stderr.write((error && error.stack) || String(error));
  process.exit(1);
});
'''


class DesignerRequestTimeoutTest(unittest.TestCase):
    maxDiff = None

    def _require_engine(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is required to run the designer request harness")
        probe = subprocess.run(
            [node, "-e", "require.resolve('jsdom')"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            self.skipTest("jsdom is required to run the designer request harness")
        return node

    def test_request_times_out_and_passes_an_aborted_signal(self):
        self.assertTrue(os.path.isfile(DESIGNER_PATH), "designer.js not found")
        node = self._require_engine()
        with tempfile.TemporaryDirectory() as tmp:
            harness = os.path.join(tmp, "designer_request_harness.js")
            with open(harness, "w", encoding="utf-8") as handle:
                handle.write(NODE_HARNESS)
            result = subprocess.run(
                [node, harness, DESIGNER_PATH],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertEqual(
            result.returncode,
            0,
            msg="harness failed:\n%s\n%s" % (result.stdout, result.stderr),
        )
        data = json.loads(result.stdout)
        self.assertTrue(data["rejected"], "request() should reject after the timeout")
        self.assertEqual(data["message"], "request timed out")
        self.assertEqual(data["messages"], ["request timed out"])
        self.assertTrue(data["signalProvided"], "fetch() must receive an AbortSignal")
        self.assertTrue(data["signalAborted"], "the AbortSignal must be aborted at timeout")
        self.assertTrue(data["fetchCalls"] >= 1, "fetch() must be invoked")
        self.assertIn("presets", data["url"])


if __name__ == "__main__":
    unittest.main()
