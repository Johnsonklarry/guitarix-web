"""Issue #599: the Listen control must actually start and stop audio.

The broadcast client is plain browser JavaScript, so this test drives it in a
jsdom document built from the real templates/broadcast.html markup. The PCM
transport is replaced with counters after the script loads (the page publishes
window.gxPcm itself), a click is dispatched on #stream, and the resulting state
of the control and the status row is checked. No network is touched: the page's
socket.io call is stubbed to an inert object and jsdom is run with scripts
inert except for the one file we evaluate by hand.
"""

import json
import os
import shutil
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Node driver. It prints one JSON object on stdout describing the state after
# the first and the second click on #stream.
NODE_DRIVER = r'''
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const root = process.env.BROADCAST_ROOT;

const html = fs.readFileSync(path.join(root, 'templates', 'broadcast.html'), 'utf8');
const client = fs.readFileSync(path.join(root, 'static', 'broadcast.js'), 'utf8');

// runScripts: 'outside-only' keeps every <script> tag in the markup inert, so
// nothing is fetched; the client is evaluated explicitly below.
const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://localhost/' });
const { window } = dom;

// socket.io is a network client and has no part in this test. The page only
// needs an object that answers .on() at load time.
window.io = function () {
  return { on: function () {}, off: function () {}, emit: function () {} };
};

// Run broadcast.js the way a <script> would, inside the window's own realm.
vm.runInContext(client, dom.getInternalVMContext(), { filename: 'broadcast.js' });

// The page publishes window.gxPcm itself; replace it *after* load so the click
// handler finds counters instead of the real PCM transport.
let started = 0;
let stopped = 0;
window.gxPcm = {
  start: function () { started += 1; },
  stop: function () { stopped += 1; }
};

const stream = window.document.getElementById('stream');
const status = window.document.getElementById('status');

function click() {
  stream.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
}

click();
const first = {
  started: started,
  stopped: stopped,
  pressed: stream.getAttribute('aria-pressed'),
  onClass: stream.classList.contains('on'),
  status: status.textContent
};

click();
const second = {
  started: started,
  stopped: stopped,
  pressed: stream.getAttribute('aria-pressed'),
  onClass: stream.classList.contains('on'),
  status: status.textContent
};

process.stdout.write(JSON.stringify({ first: first, second: second }));
'''


class TestBroadcastListenControl(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which('node')
        if self.node is None:
            self.skipTest('node is not installed')
        probe = subprocess.run(
            [self.node, '-e', "require.resolve('jsdom')"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        if probe.returncode != 0:
            self.skipTest('the jsdom node package is not installed')

    def _run_driver(self):
        env = dict(os.environ)
        env['BROADCAST_ROOT'] = REPO_ROOT
        proc = subprocess.run(
            [self.node, '-e', NODE_DRIVER],
            cwd=REPO_ROOT, capture_output=True, text=True, env=env, timeout=60,
        )
        if proc.returncode != 0:
            self.fail('node driver exited %d\nstderr:\n%s' % (proc.returncode, proc.stderr))
        return json.loads(proc.stdout)

    def test_click_toggles_listen_and_status(self):
        result = self._run_driver()
        first = result['first']
        second = result['second']

        self.assertEqual(first['started'], 1, 'pcmStart should run on the first click')
        self.assertEqual(first['stopped'], 0, 'pcmStop must not run on the first click')
        self.assertEqual(first['pressed'], 'true')
        self.assertTrue(first['onClass'], '#stream should carry the on class while playing')
        self.assertNotEqual(first['status'].strip(), '', '#status should report playback')

        self.assertEqual(second['stopped'], 1, 'pcmStop should run on the second click')
        self.assertEqual(second['started'], 1, 'the second click must not start again')
        self.assertEqual(second['pressed'], 'false')
        self.assertFalse(second['onClass'], 'the on class should be cleared on stop')


if __name__ == '__main__':
    unittest.main()
