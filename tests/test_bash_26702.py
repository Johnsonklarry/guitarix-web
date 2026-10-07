"""Sequencing of numbered state updates in the client (issue #26702).

static/app.js runs in a browser and this suite is stdlib-only Python, so the
tests drive the client through the entry points it exposes to the socket: the
bodies of the snapshot/params/connect/resync handlers are read out of the
source and checked for what they do with a version -- which handler applies a
sequential delta, which one asks for a full snapshot when a version has been
skipped, and what a reconnect tells the engine.
"""

import re
import unittest
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / 'static' / 'app.js'

# `if (... stateVersion + 1 ...)`: the guard that spots a missed update.
GAP_GUARD = r'if\s*\([^)]*stateVersion\s*\+\s*1[^)]*\)'


def _balanced(src, index):
    """`src[index:]` up to and including the brace that closes `src[index]`."""
    depth = 0
    for i in range(index, len(src)):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return src[index:i + 1]
    raise AssertionError('unbalanced braces at %r' % src[index:index + 40])


def handler_body(src, event):
    """Body of `socket.on('<event>', function (...) { ... })`."""
    start = src.find("socket.on('%s'" % event)
    if start < 0:
        raise AssertionError("app.js has no socket.on('%s') handler" % event)
    return _balanced(src, src.index('{', start))[1:-1]


def function_body(src, name):
    """Body of `function <name>(...) { ... }`."""
    m = re.search(r'function\s+' + re.escape(name) + r'\s*\(', src)
    if not m:
        raise AssertionError('app.js has no function %s()' % name)
    return _balanced(src, src.index('{', m.end()))[1:-1]


def branch_body(body, guard):
    """Inner text of the first block whose `if (...)` matches `guard`."""
    m = re.search(guard, body)
    if not m:
        raise AssertionError('no branch matching %r' % guard)
    return _balanced(body, body.index('{', m.end()))[1:-1]


class DeltaSequencingTest(unittest.TestCase):
    """The socket entry points, and what each does with a state version."""

    @classmethod
    def setUpClass(cls):
        cls.src = APP_JS.read_text(encoding='utf-8')
        cls.apply = function_body(cls.src, 'applyValues')
        cls.accept = function_body(cls.src, 'acceptDelta')

    # ---- snapshot: the version the page holds is the one it just adopted

    def test_snapshot_adopts_the_version_it_carries(self):
        body = handler_body(self.src, 'snapshot')
        self.assertRegex(body, r'stateVersion\s*=\s*[^;\n]*snap\.version')
        self.assertRegex(body, r'resyncing\s*=\s*false')
        self.assertRegex(self.src, r'\blet\s+stateVersion\b')

    # ---- params: deltas land in applyValues, behind the version gate

    def test_params_deltas_enter_through_the_versioned_gate(self):
        body = handler_body(self.src, 'params')
        self.assertRegex(body, r'applyValues\(\s*changes\s*,\s*true\s*\)')
        self.assertLess(self.apply.index('acceptDelta('),
                        self.apply.index('Object.keys('),
                        'the version is checked before any value is written')

    # ---- snapshot then the very next delta: applied, version advanced

    def test_sequential_delta_after_a_snapshot_is_applied(self):
        gap = branch_body(self.accept, GAP_GUARD)
        tail = self.accept[self.accept.index(gap) + len(gap):]
        self.assertIn('stateVersion = delta.version', tail)
        self.assertIn('return delta.values', tail)

    # ---- a delta whose version skips one: snapshot, nothing applied

    def test_skipped_version_asks_for_a_full_snapshot(self):
        gap = branch_body(self.accept, GAP_GUARD)
        self.assertIn('requestSnapshot(', gap)
        self.assertIn('return null', gap)
        self.assertNotIn('applyValues(', gap)
        self.assertNotRegex(gap, r'stateVersion\s*=(?!=)',
                            'a gap must not move the version we hold')
        request = function_body(self.src, 'requestSnapshot')
        self.assertRegex(request, r"socket\.emit\(\s*'request_snapshot'")

    def test_unnumbered_delta_is_still_applied(self):
        self.assertRegex(
            self.accept,
            r'delta\.version\s*===\s*null\s*\)\s*return\s+delta\.values')

    # ---- reconnect: where we are, and who decides a full sync is needed

    def test_connect_sends_the_last_known_version(self):
        body = handler_body(self.src, 'connect')
        self.assertRegex(body, r"socket\.emit\(\s*'sync'")
        self.assertIn('stateVersion', body)

    def test_server_can_demand_a_full_sync(self):
        body = handler_body(self.src, 'resync')
        self.assertIn('requestSnapshot(', body)


if __name__ == '__main__':
    unittest.main()
