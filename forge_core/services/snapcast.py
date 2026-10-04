"""Minimal Snapcast JSON-RPC client with a pluggable byte transport.

The client speaks the Snapcast wire protocol: newline-delimited JSON-RPC
messages (``{"id": ..., "jsonrpc": "2.0", "method": ...}``). The transport is
injected so callers can use a real socket or the in-process fake from
:mod:`forge_core.emulate`.
"""

from __future__ import annotations

import json


class SnapcastError(RuntimeError):
    """Raised when the Snapcast server returns a JSON-RPC error."""

    def __init__(self, code, message, data=None):
        super().__init__(f'Snapcast JSON-RPC error {code}: {message}')
        self.code = code
        self.message = message
        self.data = data


class SnapcastClient:
    """Issue Snapcast JSON-RPC requests through a pluggable transport.

    ``transport`` must provide ``request(payload: bytes) -> bytes`` carrying
    one newline-terminated JSON-RPC message and returning the response with the
    same framing.
    """

    def __init__(self, transport):
        self.transport = transport
        self._next_id = 0

    def call(self, method, params=None):
        """Send one JSON-RPC request and return its ``result``."""
        self._next_id += 1
        message = {'id': self._next_id, 'jsonrpc': '2.0', 'method': method}
        if params is not None:
            message['params'] = params
        payload = (json.dumps(message) + '\n').encode('utf-8')
        return self._parse(method, self.transport.request(payload))

    def get_status(self):
        """Return the full ``Server.GetStatus`` result."""
        return self.call('Server.GetStatus')

    def list_groups(self):
        """Return the groups reported by ``Server.GetStatus``."""
        return self.get_status().get('groups', [])

    def list_clients(self):
        """Return every client reported by ``Server.GetStatus``.

        Snapcast nests clients inside groups; a flat top-level ``clients`` key
        is honoured too when present.
        """
        status = self.get_status()
        if status.get('clients') is not None:
            return status['clients']
        clients = []
        for group in status.get('groups', []):
            clients.extend(group.get('clients', []))
        return clients

    @staticmethod
    def _parse(method, response):
        if isinstance(response, (bytes, bytearray)):
            response = response.decode('utf-8')
        for line in str(response).splitlines():
            line = line.strip()
            if not line:
                continue
            message = json.loads(line)
            if 'method' in message and 'id' not in message:
                continue  # server notification
            error = message.get('error')
            if error:
                raise SnapcastError(error.get('code'), error.get('message', ''), error.get('data'))
            if 'result' in message:
                return message['result']
        raise SnapcastError(-32700, f'no JSON-RPC response for {method}')
