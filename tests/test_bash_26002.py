import os
import re
import unittest

APP_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'app.js')


def read_app():
    with open(APP_JS, 'r', encoding='utf-8') as fh:
        return fh.read()


class SharedCommandAdapterTest(unittest.TestCase):
    """Issue #26002: one adapter routes discrete writes immediately and
    continuous writes through the animation-frame outbox batcher."""

    def setUp(self):
        self.src = read_app()

    def test_command_adapter_defined(self):
        self.assertIn('function command(id, value, kind)', self.src)

    def test_command_routes_discrete_immediately(self):
        body = self.src.split('function command(id, value, kind)', 1)[1]
        body = body.split('\n}', 1)[0]
        self.assertIn("kind === 'discrete'", body)
        self.assertIn("socket.emit('set_param', { id: id, value: value })", body)
        self.assertIn('send(id, value)', body)

    def test_no_direct_emits_in_listeners(self):
        # every listener-level write must go through the adapter
        self.assertNotIn("socket.emit('set_param', { id: group.toggle, value: next })", self.src)
        self.assertNotIn("socket.emit('set_param', { id: ctrl.id, value: next })", self.src)
        self.assertNotIn("socket.emit('set_param', { id: ctrl.id, value: value })", self.src)

    def test_discrete_listeners_use_command(self):
        self.assertIn("command(group.toggle, next, 'discrete')", self.src)
        self.assertIn("command(ctrl.id, next, 'discrete')", self.src)
        self.assertIn("command(ctrl.id, value, 'discrete')", self.src)

    def test_send_still_batches(self):
        self.assertIn('requestAnimationFrame', self.src)
        self.assertIn('outbox[id] = value', self.src)

    def test_slider_input_uses_send(self):
        # makeRange's input handler must still route through the batcher
        self.assertRegex(self.src, r"input\.addEventListener\('input',[\s\S]{0,400}?send\(")


if __name__ == '__main__':
    unittest.main()
