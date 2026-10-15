"""Issue #440: a Retry action inside the tile unavailable state.

These tests load ``forge_core/static/layout.js`` inside a tiny DOM harness that
is executed by Node.js.  The harness implements only the handful of DOM
primitives that ``ForgeLayout.renderUnavailable`` touches, so no browser, no
network access and no third-party package are required.
"""

import json
import os
import shutil
import subprocess
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
LAYOUT_JS = os.path.join(REPO_ROOT, "forge_core", "static", "layout.js")


_HARNESS = r"""
'use strict';

const fs = require('fs');

class Element {
  constructor(tagName) {
    this.tagName = String(tagName).toUpperCase();
    this.childNodes = [];
    this.style = {};
    this.dataset = {};
    this.className = '';
    this.parentNode = null;
    this._textContent = '';
    this._innerHTML = '';
    this._listeners = {};
  }

  appendChild(child) {
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }

  removeChild(child) {
    const index = this.childNodes.indexOf(child);
    if (index !== -1) {
      this.childNodes.splice(index, 1);
      child.parentNode = null;
    }
    return child;
  }

  addEventListener(type, handler) {
    if (!this._listeners[type]) {
      this._listeners[type] = [];
    }
    this._listeners[type].push(handler);
  }

  removeEventListener(type, handler) {
    const handlers = this._listeners[type] || [];
    const index = handlers.indexOf(handler);
    if (index !== -1) {
      handlers.splice(index, 1);
    }
  }

  dispatchEvent(event) {
    const handlers = (this._listeners[event.type] || []).slice();
    for (const handler of handlers) {
      handler.call(this, event);
    }
    return true;
  }

  click() {
    return this.dispatchEvent({ type: 'click', target: this });
  }

  set textContent(value) {
    this._textContent = value == null ? '' : String(value);
    this.childNodes = [];
  }

  get textContent() {
    return this._textContent;
  }

  set innerHTML(value) {
    this._innerHTML = value == null ? '' : String(value);
    if (this._innerHTML === '') {
      this.childNodes = [];
    }
  }

  get innerHTML() {
    return this._innerHTML;
  }

  descendants() {
    const found = [];
    for (const child of this.childNodes) {
      found.push(child);
      for (const nested of child.descendants()) {
        found.push(nested);
      }
    }
    return found;
  }

  find(tagName) {
    const wanted = String(tagName).toUpperCase();
    return this.descendants().filter((element) => element.tagName === wanted);
  }
}

const document = {
  createElement(tagName) {
    return new Element(tagName);
  },
};

const window = {
  innerWidth: 1200,
  addEventListener() {},
  removeEventListener() {},
  document: document,
  ForgeLayout: null,
};

global.window = window;
global.document = document;

const layoutPath = process.env.FORGE_LAYOUT_JS;
if (!layoutPath) {
  process.stderr.write('FORGE_LAYOUT_JS is not set\n');
  process.exit(2);
}

const source = fs.readFileSync(layoutPath, 'utf8');
// Indirect eval runs layout.js in the global scope so that the bare `window`
// and `document` references inside it resolve to the stubs installed above.
(0, eval)(source);

const layout = window.ForgeLayout;
if (!layout || typeof layout.renderUnavailable !== 'function') {
  process.stderr.write('ForgeLayout.renderUnavailable is missing\n');
  process.exit(3);
}

const results = {};

// Case 1: supplying onRetry renders a button that fires exactly once.
{
  const bodyEl = document.createElement('div');
  let calls = 0;
  const onRetry = function onRetry() {
    calls += 1;
  };
  layout.renderUnavailable(bodyEl, 'Unavailable', onRetry);
  const buttons = bodyEl.find('button');
  results.case1_button_count = buttons.length;
  if (buttons.length === 1) {
    buttons[0].click();
  }
  results.case1_calls = calls;
}

// Case 2: no callback means no interactive element is rendered.
{
  const bodyEl = document.createElement('div');
  layout.renderUnavailable(bodyEl, 'Unavailable');
  results.case2_button_count = bodyEl.find('button').length;
}

process.stdout.write(JSON.stringify(results));
"""


class RenderUnavailableRetryTests(unittest.TestCase):
    def _run_harness(self):
        if not os.path.isfile(LAYOUT_JS):
            self.fail("layout.js not found at %s" % (LAYOUT_JS,))
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required to execute the DOM harness")
        env = dict(os.environ)
        env["FORGE_LAYOUT_JS"] = LAYOUT_JS
        proc = subprocess.run(
            [node, "-e", _HARNESS],
            capture_output=True,
            text=True,
            env=env,
            cwd=REPO_ROOT,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg="DOM harness failed:\nstdout: %s\nstderr: %s"
            % (proc.stdout, proc.stderr),
        )
        try:
            return json.loads(proc.stdout)
        except ValueError:
            self.fail("DOM harness did not emit JSON: %r" % (proc.stdout,))

    def test_retry_button_present_and_callback_called_once(self):
        data = self._run_harness()
        self.assertEqual(data["case1_button_count"], 1)
        self.assertEqual(data["case1_calls"], 1)

    def test_no_button_without_retry_callback(self):
        data = self._run_harness()
        self.assertEqual(data["case2_button_count"], 0)


if __name__ == "__main__":
    unittest.main()
