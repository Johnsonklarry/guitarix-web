import json
import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakes.engine import FakeEngine


class _RecorderSocket:
    """Minimal stand-in for a client socket.

    Records everything the engine sends so tests can assert on the observable
    response (or the absence of one) without touching the network.
    """

    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, data):
        if self.closed:
            raise OSError('socket closed')
        self.sent.append(data)

    def close(self):
        self.closed = True

    def messages(self):
        out = []
        for chunk in self.sent:
            if isinstance(chunk, bytes):
                chunk = chunk.decode('utf-8')
            for line in chunk.splitlines():
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


def _call(method, params, call_id=1):
    return json.dumps({'jsonrpc': '2.0', 'id': call_id,
                       'method': method, 'params': params})


def _notify(method, params):
    return json.dumps({'jsonrpc': '2.0', 'method': method, 'params': params})


class FaultInjectionTest(unittest.TestCase):
    def setUp(self):
        # Keep the engine off the network; we drive _process_request directly.
        os.environ['GX_PORT'] = '0'
        self.engine = FakeEngine()

    def tearDown(self):
        os.environ.pop('GX_PORT', None)

    # --- baseline: no faults configured means nothing changes -------------

    def test_no_faults_configured_returns_normal_response(self):
        sock = _RecorderSocket()
        self.engine._process_request(sock, _call('get', ['amp.fuzz']))

        msgs = sock.messages()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]['id'], 1)
        self.assertEqual(msgs[0]['result'], {'amp.fuzz': 0.0})
        self.assertEqual(self.engine.fault_log, [])

    def test_no_faults_configured_still_broadcasts(self):
        sender = _RecorderSocket()
        other = _RecorderSocket()
        self.engine.clients.add(sender)
        self.engine.clients.add(other)

        self.engine._process_request(sender, _notify('set', ['amp.fuzz', 0.7]))

        self.assertEqual(sender.sent, [])
        self.assertEqual(len(other.sent), 1)
        broadcast = other.messages()[0]
        self.assertEqual(broadcast['method'], 'set')
        self.assertEqual(broadcast['params'], ['amp.fuzz', 0.7])
        self.assertEqual(self.engine.fault_log, [])

    # --- delay ------------------------------------------------------------

    def test_delay_fault_is_logged_and_response_still_sent(self):
        self.engine.faults = {'get': {'delay': 0.05}}
        sock = _RecorderSocket()

        start = time.monotonic()
        self.engine._process_request(sock, _call('get', ['amp.fuzz']))
        elapsed = time.monotonic() - start

        self.assertGreaterEqual(elapsed, 0.05)
        self.assertEqual(self.engine.fault_log,
                         [{'method': 'get', 'fault': 'delay', 'detail': 0.05}])
        msgs = sock.messages()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]['result'], {'amp.fuzz': 0.0})

    def test_delay_fault_is_per_method(self):
        self.engine.faults = {'get': {'delay': 0.05}}
        sock = _RecorderSocket()

        # 'set' has no fault configured, so it must not be delayed or logged.
        self.engine._process_request(sock, _notify('set', ['amp.fuzz', 0.3]))

        self.assertEqual(self.engine.fault_log, [])
        self.assertEqual(self.engine.values['amp.fuzz'], 0.3)

    # --- drop -------------------------------------------------------------

    def test_drop_fault_suppresses_response_and_is_logged(self):
        self.engine.faults = {'get': {'drop': True}}
        sock = _RecorderSocket()

        self.engine._process_request(sock, _call('get', ['amp.fuzz']))

        self.assertEqual(sock.sent, [])
        self.assertEqual(self.engine.fault_log,
                         [{'method': 'get', 'fault': 'drop'}])

    def test_drop_fault_suppresses_broadcast(self):
        self.engine.faults = {'set': {'drop': True}}
        sender = _RecorderSocket()
        other = _RecorderSocket()
        self.engine.clients.add(sender)
        self.engine.clients.add(other)

        self.engine._process_request(sender, _notify('set', ['amp.fuzz', 0.9]))

        self.assertEqual(sender.sent, [])
        self.assertEqual(other.sent, [])
        self.assertEqual(self.engine.fault_log,
                         [{'method': 'set', 'fault': 'drop'}])

    def test_drop_fault_does_not_mutate_state(self):
        self.engine.faults = {'set': {'drop': True}}
        sock = _RecorderSocket()

        self.engine._process_request(sock, _notify('set', ['amp.fuzz', 0.9]))

        self.assertEqual(self.engine.values['amp.fuzz'], 0.0)

    def test_drop_fault_on_setpreset_leaves_preset_untouched(self):
        self.engine.faults = {'setpreset': {'drop': True}}
        sock = _RecorderSocket()

        self.engine._process_request(
            sock, _notify('setpreset', ['Warm', 'Clean Warm']))

        self.assertEqual(self.engine.values['amp.fuzz'], 0.0)
        self.assertEqual(self.engine.values['system.current_preset'], '')
        self.assertEqual(self.engine.fault_log,
                         [{'method': 'setpreset', 'fault': 'drop'}])

    # --- combined / ordering ---------------------------------------------

    def test_delay_and_drop_are_both_logged_in_order(self):
        self.engine.faults = {'get': {'delay': 0.01, 'drop': True}}
        sock = _RecorderSocket()

        self.engine._process_request(sock, _call('get', ['amp.fuzz']))

        self.assertEqual(sock.sent, [])
        self.assertEqual(self.engine.fault_log, [
            {'method': 'get', 'fault': 'delay', 'detail': 0.01},
            {'method': 'get', 'fault': 'drop'},
        ])

    def test_fault_log_accumulates_across_requests(self):
        self.engine.faults = {'get': {'drop': True}}
        sock = _RecorderSocket()

        self.engine._process_request(sock, _call('get', ['amp.fuzz'], call_id=1))
        self.engine._process_request(sock, _call('get', ['amp.fuzz'], call_id=2))

        self.assertEqual(len(self.engine.fault_log), 2)
        self.assertTrue(all(e['fault'] == 'drop' for e in self.engine.fault_log))

    def test_disabling_faults_restores_normal_behaviour(self):
        sock = _RecorderSocket()
        self.engine.faults = {'get': {'drop': True}}
        self.engine._process_request(sock, _call('get', ['amp.fuzz']))
        self.assertEqual(sock.sent, [])

        self.engine.faults = {}
        self.engine._process_request(sock, _call('get', ['amp.fuzz']))

        msgs = sock.messages()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]['result'], {'amp.fuzz': 0.0})


class FaultInjectionOverSocketTest(unittest.TestCase):
    """End-to-end check that faults are observable through a real socket."""

    def setUp(self):
        self.engine = FakeEngine()
        self.engine.port = 0
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            client, _ = self.server.accept()
        except OSError:
            return
        self.engine._handle_client(client)

    def tearDown(self):
        self.engine.running = False
        try:
            self.server.close()
        except OSError:
            pass
        self.thread.join(timeout=2)

    def _roundtrip(self, payload, expect_reply=True):
        client = socket.create_connection(('127.0.0.1', self.port), timeout=2)
        try:
            client.sendall((payload + '\n').encode('utf-8'))
            if not expect_reply:
                time.sleep(0.1)
                return None
            client.settimeout(2)
            buf = b''
            while b'\n' not in buf:
                chunk = client.recv(4096)
                if not chunk:
                    break
                buf += chunk
            if b'\n' not in buf:
                return None
            line, _ = buf.split(b'\n', 1)
            return json.loads(line.decode('utf-8'))
        finally:
            client.close()

    def test_dropped_get_yields_no_reply_and_logs_fault(self):
        self.engine.faults = {'get': {'drop': True}}
        reply = self._roundtrip(_call('get', ['amp.fuzz']), expect_reply=False)
        self.assertIsNone(reply)
        self.assertEqual(self.engine.fault_log,
                         [{'method': 'get', 'fault': 'drop'}])

    def test_normal_get_over_socket_still_works(self):
        reply = self._roundtrip(_call('get', ['amp.fuzz']))
        self.assertIsNotNone(reply)
        self.assertEqual(reply['result'], {'amp.fuzz': 0.0})
        self.assertEqual(self.engine.fault_log, [])


if __name__ == '__main__':
    unittest.main()
