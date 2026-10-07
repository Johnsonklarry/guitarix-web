import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(ROOT, 'static', 'app.js')


def read_app_js():
    with open(APP_JS, 'r', encoding='utf-8') as fh:
        return fh.read()


def handler_body(src, event):
    """Return the body of socket.on('<event>', function (...) { ... });"""
    m = re.search(r"socket\.on\('" + re.escape(event) + r"', function \([^)]*\) \{(.*?)\n\}\);",
                  src, re.S)
    if m is None:
        raise AssertionError("socket.on('%s', ...) handler not found" % event)
    return m.group(1)


class TestStateVersionInitialised(unittest.TestCase):
    def setUp(self):
        self.src = read_app_js()

    def test_state_object_declares_version(self):
        m = re.search(r"let state = \{(.*?)\};", self.src, re.S)
        self.assertIsNotNone(m, 'state declaration not found')
        self.assertIn('version', m.group(1))

    def test_version_reads_are_guarded(self):
        # state.version is read before any snapshot arrives, so reads must
        # fall back to a default rather than yielding undefined/NaN.
        self.assertIn('(state.version || 0)', self.src)


class TestSnapshotRecordsVersion(unittest.TestCase):
    def setUp(self):
        self.src = read_app_js()

    def test_snapshot_records_version(self):
        body = handler_body(self.src, 'snapshot')
        self.assertIn('state.version = snap.version', body)

    def test_snapshot_still_copies_and_renders(self):
        body = handler_body(self.src, 'snapshot')
        self.assertIn('state = Object.assign({}, snap);', body)
        self.assertIn('applyValues(snap.values || {}, false);', body)

    def test_snapshot_version_assignment_precedes_rendering(self):
        body = handler_body(self.src, 'snapshot')
        assign = body.index('state.version = snap.version')
        render = body.index('applyValues(snap.values || {}, false);')
        self.assertLess(assign, render)


class TestDeltaHandler(unittest.TestCase):
    def setUp(self):
        self.src = read_app_js()

    def _delta_body(self):
        return handler_body(self.src, 'delta')

    def test_delta_handler_exists(self):
        self.assertIn("socket.on('delta'", self.src)

    def test_delta_applies_changes_via_apply_values(self):
        body = self._delta_body()
        self.assertIn('applyValues(msg.changes || {}, true);', body)

    def test_delta_checks_sequence(self):
        body = self._delta_body()
        self.assertIn('const expected = (state.version || 0) + 1;', body)
        self.assertIn('if (msg.version !== expected)', body)

    def test_delta_updates_version(self):
        body = self._delta_body()
        self.assertIn('state.version = msg.version;', body)

    def test_delta_requests_resync_on_gap(self):
        body = self._delta_body()
        self.assertIn("socket.emit('resync'", body)
        # the resync must be the gap branch, not the happy path
        gap = body.index('if (msg.version !== expected)')
        resync = body.index("socket.emit('resync'")
        apply = body.index('applyValues(msg.changes || {}, true);')
        self.assertLess(gap, resync)
        self.assertLess(resync, apply)

    def test_delta_does_not_apply_out_of_order(self):
        body = self._delta_body()
        # applyValues must sit after the guard, so a gap never mutates state
        guard = body.index('if (msg.version !== expected)')
        apply = body.index('applyValues(msg.changes || {}, true);')
        self.assertLess(guard, apply)

    def test_delta_version_updated_after_apply(self):
        body = self._delta_body()
        apply = body.index('applyValues(msg.changes || {}, true);')
        bump = body.index('state.version = msg.version;')
        self.assertLess(apply, bump)


class TestReconnectSendsVersion(unittest.TestCase):
    def setUp(self):
        self.src = read_app_js()

    def test_connect_handler_present(self):
        self.assertIn("socket.on('connect'", self.src)

    def test_connect_handler_sends_last_known_version(self):
        body = handler_body(self.src, 'connect')
        self.assertIn("socket.emit('resync'", body)
        self.assertIn('state.version || 0', body)

    def test_connect_handler_still_marks_linked(self):
        body = handler_body(self.src, 'connect')
        self.assertIn('setLinked(true);', body)

    def test_disconnect_handler_unchanged(self):
        self.assertIn("socket.on('disconnect', function () { setLinked(false); });",
                      self.src)


if __name__ == '__main__':
    unittest.main()
