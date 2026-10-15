"""Regression tests for issue #408: cellAt() must bail out on a zero-width grid.

``forge_core/static/designer.js`` is a browser IIFE with no exports, so this
test evaluates the real file under a small Node harness that installs DOM and
network stubs, exposes the internal ``cellAt`` helper, and inspects its return
value.  The stubbed ``fetch`` never resolves, so ``init()`` suspends before any
request is made and no network access occurs.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DESIGNER_JS = REPO_ROOT / "forge_core" / "static" / "designer.js"

HARNESS = r"""
'use strict';

const fs = require('fs');
const sourcePath = process.argv[2];

let src = fs.readFileSync(sourcePath, 'utf8').replace(/\s+$/, '');
const closing = '})();';
if (!src.endsWith(closing)) {
  throw new Error('unexpected designer.js entry point shape');
}
src = src.slice(0, -closing.length) +
  '\nglobalThis.__designer = {cellAt: cellAt, grid: grid, setPreset: function (value) { preset = value; }};\n' +
  closing;

const elements = new Map();

function makeElement(id) {
  return {
    id: id,
    style: {setProperty: function () {}},
    classList: {
      toggle: function () {},
      add: function () {},
      remove: function () {},
      contains: function () { return false; }
    },
    dataset: {},
    hidden: false,
    value: '',
    textContent: '',
    checked: false,
    addEventListener: function () {},
    removeEventListener: function () {},
    setAttribute: function () {},
    removeAttribute: function () {},
    append: function () {},
    replaceChildren: function () {},
    focus: function () {},
    setPointerCapture: function () {},
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    getBoundingClientRect: function () { return globalThis.__rect; }
  };
}

globalThis.__rect = {left: 0, top: 0, right: 0, width: 0, bottom: 0, height: 0};

globalThis.document = {
  title: '',
  getElementById: function (id) {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  createElement: function (tag) { return makeElement(tag); },
  addEventListener: function () {}
};

globalThis.window = globalThis;
globalThis.location = {href: 'http://localhost/designer.html', search: ''};
globalThis.addEventListener = function () {};
globalThis.confirm = function () { return false; };
globalThis.prompt = function () { return null; };
globalThis.fetch = function () { return new Promise(function () {}); };
globalThis.structuredClone = function (value) { return JSON.parse(JSON.stringify(value)); };

(0, eval)(src);

const designer = globalThis.__designer;
designer.setPreset({
  version: 1, id: 'probe', name: 'probe', dashboard: 'probe',
  columns: 12, row_height: 80, tiles: []
});

globalThis.__rect = {left: 0, top: 0, right: 200, width: 0, bottom: 400, height: 400};
const zero = designer.cellAt(50, 50);

globalThis.__rect = {left: 0, top: 0, right: 384, width: 384, bottom: 480, height: 480};
const positive = designer.cellAt(100, 100);

function describe(value) {
  if (value === null || value === undefined) return 'NULL';
  return 'OBJ ' + value.x + ' ' + value.y;
}

process.stdout.write('ZERO=' + describe(zero) + '\n');
process.stdout.write('POSITIVE=' + describe(positive) + '\n');
"""


class CellAtZeroWidthTests(unittest.TestCase):
    """cellAt() must not manufacture Infinite/NaN coordinates."""

    def _run_harness(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('node is required to evaluate designer.js')
        with tempfile.TemporaryDirectory() as tmp:
            harness_path = Path(tmp) / 'designer_harness.js'
            harness_path.write_text(HARNESS, encoding='utf-8')
            completed = subprocess.run(
                [node, str(harness_path), str(DESIGNER_JS)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        values = {}
        for line in completed.stdout.splitlines():
            if '=' in line:
                key, _, value = line.partition('=')
                values[key.strip()] = value.strip()
        return values

    def test_zero_width_rect_returns_no_cell(self):
        values = self._run_harness()
        self.assertIn('ZERO', values)
        self.assertEqual(values['ZERO'], 'NULL', values)
        self.assertNotIn('Infinity', values['ZERO'])
        self.assertNotIn('NaN', values['ZERO'])

    def test_positive_width_rect_returns_finite_cell(self):
        values = self._run_harness()
        self.assertEqual(values.get('POSITIVE'), 'OBJ 3 1', values)


if __name__ == '__main__':
    unittest.main()
