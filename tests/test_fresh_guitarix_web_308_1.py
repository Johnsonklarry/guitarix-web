"""Tests for issue #308: the preset browser keeps a "Recently used" section.

The demo is plain JavaScript, so these tests drive it through a small Node
harness that stubs the browser globals, loads static/demo.js, connects through
the demo's io() stand-in, switches presets the way the page would (via the
demo's own set_preset message handling), and prints the bank list the demo
emits.  The demo never opens a socket, so nothing here touches the network.
"""

import json
import os
import shutil
import subprocess
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_JS = os.path.join(REPO_ROOT, 'static', 'demo.js')

REAL_BANKS = ['Clean & Warm', 'Rock', 'Ambient', 'Folk', 'Heavy', 'Scratchpad']
RECENT_NAME = 'Recently used'


# Node harness.  It reads the demo path and the preset-switch sequence from the
# environment, stubs window/document/URL/Blob/timers so demo.js runs headless,
# appends a tiny scenario that drives io(), and prints the emitted bank list.
HARNESS = r"""
var fs = require('fs');
var vm = require('vm');

var demoPath = process.env.DEMO_JS_PATH;
var seq = JSON.parse(process.env.DEMO_SEQ || '[]');
var demoSource = fs.readFileSync(demoPath, 'utf8');

global.window = global;
global.document = {
  getElementById: function () { return null; },
  createElement: function () { return {}; }
};
global.URL = {
  createObjectURL: function () { return 'blob:demo'; },
  revokeObjectURL: function () {}
};
global.Blob = function () {};
global.Response = function () {};
global.fetch = null;

/* Timers are queued, never run on their own, so the scenario can attach its
   handlers before flushing the demo's connect/snapshot/preset callbacks. */
global.__timers = [];
global.setTimeout = function (fn) { global.__timers.push(fn); return global.__timers.length; };
global.clearTimeout = function () {};
global.setInterval = function () { return 0; };
global.clearInterval = function () {};
global.__flush = function () {
  var guard = 0;
  while (global.__timers.length && guard++ < 100000) {
    global.__timers.shift()();
  }
};

var scenarioLines = [
  "var __events = {};",
  "var __socket = io();",
  "__socket.on('snapshot', function (d) { __events.snapshot = d; });",
  "__socket.on('banks', function (d) { __events.banks = d; });",
  "__socket.on('preset', function (d) { __events.preset = d; });",
  "__flush();",
  "var __seq = " + JSON.stringify(seq) + ";",
  "__seq.forEach(function (p) {",
  "  __socket.emit('set_preset', { bank: p[0], preset: p[1] });",
  "  __flush();",
  "});",
  "console.log(JSON.stringify({ snapshot: __events.snapshot, banks: __events.banks }));"
];

vm.runInThisContext(demoSource + '\n' + scenarioLines.join('\n'), { filename: 'demo_scenario.js' });
"""


class RecentlyUsedTest(unittest.TestCase):
    """The demo's bank list must lead with a "Recently used" section."""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which('node') or shutil.which('nodejs')
        if not cls.node:
            raise unittest.SkipTest('a Node.js runtime is required for the demo harness')
        if not os.path.exists(DEMO_JS):
            raise unittest.SkipTest('static/demo.js not found at %s' % DEMO_JS)

    def run_scenario(self, seq):
        """Load the demo, switch through seq, return the emitted state."""
        env = dict(os.environ)
        env['DEMO_JS_PATH'] = DEMO_JS
        env['DEMO_SEQ'] = json.dumps(seq)
        proc = subprocess.run([self.node, '-e', HARNESS],
                              capture_output=True, text=True, env=env, timeout=60)
        if proc.returncode != 0:
            self.fail('demo harness failed:\n%s' % proc.stderr)
        try:
            return json.loads(proc.stdout)
        except ValueError:
            self.fail('demo harness produced no JSON:\n%s\nstderr:\n%s'
                      % (proc.stdout, proc.stderr))

    def top_section(self, payload):
        """Pull the top-of-list section out of the state the demo emitted."""
        if payload.get('banks'):
            banks = payload['banks']['banks']
        else:
            banks = payload['snapshot']['banks']
        self.assertTrue(banks, 'the demo emitted no banks')
        return banks[0], banks[1:]

    def assert_recent_section(self, section):
        self.assertEqual(section.get('name'), RECENT_NAME)
        self.assertTrue(section.get('recent'), 'the section should be flagged as recent')
        self.assertIsInstance(section.get('presets'), list)
        presets = section['presets']
        self.assertLessEqual(len(presets), 5)
        self.assertEqual(len(presets), len(set(presets)), 'recent presets must be unique')
        return presets

    def test_initial_snapshot_lists_the_current_preset(self):
        payload = self.run_scenario([])
        section, rest = self.top_section(payload)
        presets = self.assert_recent_section(section)
        self.assertEqual(presets, ['Rock/Crunch Rhythm'])
        self.assertEqual([b['name'] for b in rest], REAL_BANKS)

    def test_section_sits_on_top_of_every_emitted_bank_list(self):
        payload = self.run_scenario([['Ambient', 'Shimmer Pad']])
        section, rest = self.top_section(payload)
        self.assert_recent_section(section)
        self.assertEqual([b['name'] for b in rest], REAL_BANKS)

    def test_five_most_recent_in_most_recent_first_order(self):
        seq = [
            ['Ambient', 'Shimmer Pad'],
            ['Folk', 'Open Tuning'],
            ['Heavy', 'Doom'],
            ['Clean & Warm', 'Glass Clean'],
            ['Rock', 'Big Dumb Rock'],
            ['Ambient', 'Slow Swell'],
            ['Rock', 'Crunch Rhythm'],
        ]
        payload = self.run_scenario(seq)
        section, _ = self.top_section(payload)
        presets = self.assert_recent_section(section)
        self.assertEqual(presets, [
            'Rock/Crunch Rhythm',
            'Ambient/Slow Swell',
            'Rock/Big Dumb Rock',
            'Clean & Warm/Glass Clean',
            'Heavy/Doom',
        ])
        self.assertEqual(len(presets), 5)

    def test_fewer_than_five_when_fewer_have_been_used(self):
        payload = self.run_scenario([['Ambient', 'Shimmer Pad']])
        section, _ = self.top_section(payload)
        presets = self.assert_recent_section(section)
        self.assertEqual(presets, ['Ambient/Shimmer Pad', 'Rock/Crunch Rhythm'])
        self.assertLess(len(presets), 5)

    def test_reusing_a_preset_moves_it_to_the_front_without_duplicating(self):
        payload = self.run_scenario([['Folk', 'Open Tuning'], ['Folk', 'Open Tuning']])
        section, _ = self.top_section(payload)
        presets = self.assert_recent_section(section)
        self.assertEqual(presets, ['Folk/Open Tuning', 'Rock/Crunch Rhythm'])
        self.assertEqual(len(presets), len(set(presets)))


if __name__ == '__main__':
    unittest.main()
