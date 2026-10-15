"""Regression test for issue #602: the recording indicator must be visible
during a take.

static/broadcast.css hides .preset__tag with ``opacity: 0``; static/broadcast.js
only toggled the tag's ``hidden`` attribute, so while a take ran the element was
unhidden but still fully transparent. The fix adds a
``.preset.is-rec .preset__tag { opacity: 1 }`` rule and makes the client toggle
``is-rec`` on the preset row while recording.

The test drives the real broadcast entry point (static/broadcast.js) inside a
throwaway JavaScript sandbox with a stubbed socket client and a stubbed DOM, so
no network, browser, audio device or guitarix instance is required.
"""

import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "static" / "broadcast.js"
CSS_PATH = ROOT / "static" / "broadcast.css"


# Minimal stubs: enough DOM for broadcast.js to boot, and an io() client that
# records the handlers the page registers instead of opening a socket.
HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const scriptPath = process.argv[2];
const source = fs.readFileSync(scriptPath, 'utf8');

function makeClassList() {
  const set = new Set();
  return {
    add: function (c) { set.add(c); },
    remove: function (c) { set.delete(c); },
    contains: function (c) { return set.has(c); },
    toggle: function (c, force) {
      if (force === undefined) {
        if (set.has(c)) { set.delete(c); } else { set.add(c); }
      } else if (force) {
        set.add(c);
      } else {
        set.delete(c);
      }
      return set.has(c);
    }
  };
}

function makeElement() {
  const el = {
    hidden: false,
    textContent: '',
    classList: makeClassList(),
    parentElement: null,
    _row: null
  };
  el.closest = function (selector) {
    return selector === '.preset' ? el._row : null;
  };
  return el;
}

const presetRow = makeElement();
const rolling = makeElement();
rolling.hidden = true;            // the tag starts hidden
rolling._row = presetRow;
rolling.parentElement = presetRow;

const elements = {
  pilot: makeElement(),
  status: makeElement(),
  bank: makeElement(),
  preset: makeElement(),
  rolling: rolling,
  takes: makeElement(),
  stream: makeElement()
};

const handlers = {};

function io() {
  return {
    on: function (event, handler) { handlers[event] = handler; return this; },
    emit: function () { throw new Error('broadcast.js must never emit'); }
  };
}

const sandbox = {
  document: {
    getElementById: function (id) {
      return Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null;
    },
    querySelector: function () { return presetRow; }
  },
  io: io,
  console: console,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout
};
sandbox.window = sandbox;

vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: scriptPath });

handlers.rec({ recording: true, count: 2 });
const started = {
  isRec: presetRow.classList.contains('is-rec'),
  hidden: rolling.hidden
};

handlers.rec({ recording: false, count: 2 });
const stopped = {
  isRec: presetRow.classList.contains('is-rec'),
  hidden: rolling.hidden
};

process.stdout.write(JSON.stringify({ started: started, stopped: stopped }));
"""


class BroadcastRecordingIndicatorTest(unittest.TestCase):

    def test_is_rec_toggles_with_the_take(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is required to drive static/broadcast.js")

        with tempfile.TemporaryDirectory() as tmp:
            harness = pathlib.Path(tmp) / "broadcast_harness.js"
            harness.write_text(HARNESS, encoding="utf-8")
            proc = subprocess.run(
                [node, str(harness), str(JS_PATH)],
                capture_output=True,
                text=True,
                timeout=30,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)

        # Starting a take: preset flagged, tag shown.
        self.assertTrue(result["started"]["isRec"])
        self.assertFalse(result["started"]["hidden"])

        # Stopping the take: both cleared.
        self.assertFalse(result["stopped"]["isRec"])
        self.assertTrue(result["stopped"]["hidden"])

    def test_css_raises_the_tag_opacity_while_recording(self):
        css = CSS_PATH.read_text(encoding="utf-8")
        # The default stays hidden.
        self.assertRegex(css, r"\.preset__tag\s*\{[^}]*opacity\s*:\s*0")
        # The recording state makes it fully visible.
        self.assertRegex(
            css,
            r"\.preset\.is-rec\s+\.preset__tag\s*\{[^}]*opacity\s*:\s*1\s*;?",
        )


if __name__ == "__main__":
    unittest.main()
