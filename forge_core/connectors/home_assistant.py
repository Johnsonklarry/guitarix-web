"""Home Assistant connector: REST states/services and WebSocket events.

Configuration is read from the environment, never from code:

* ``HOME_ASSISTANT_URL`` -- base URL, e.g. ``http://homeassistant.local:8123``
* ``HOME_ASSISTANT_TOKEN`` -- long-lived access token
* ``HOME_ASSISTANT_ENTITIES`` -- optional comma-separated entity allowlist
* ``HOME_ASSISTANT_SERVICES`` -- optional comma-separated ``domain.service``
  allowlist

The access token is never returned, logged or included in exception text.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import urllib.error
import urllib.parse
import urllib.request

__all__ = ["Client", "HomeAssistantError", "NotAllowed", "from_env"]

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class HomeAssistantError(RuntimeError):
    """Raised for Home Assistant API or transport failures."""


class NotAllowed(HomeAssistantError):
    """Raised when an entity or service is outside the caller's allowlist."""


def _split_env(value):
    if not value:
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


def _encode_frame(payload, opcode=0x1):
    """Encode a masked client WebSocket frame."""
    data = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    frame = bytearray([0x80 | opcode])
    length = len(data)
    if length < 126:
        frame.append(0x80 | length)
    elif length < 65536:
        frame.append(0x80 | 126)
        frame.extend(struct.pack("!H", length))
    else:
        frame.append(0x80 | 127)
        frame.extend(struct.pack("!Q", length))
    mask = os.urandom(4)
    frame.extend(mask)
    frame.extend(byte ^ mask[index % 4] for index, byte in enumerate(data))
    return bytes(frame)


def _recv_exact(sock, count):
    buffer = b""
    while len(buffer) < count:
        chunk = sock.recv(count - len(buffer))
        if not chunk:
            raise HomeAssistantError("WebSocket connection closed")
        buffer += chunk
    return buffer


def _read_frame(sock):
    """Read one WebSocket frame, returning ``(fin, opcode, payload)``."""
    first, second = _recv_exact(sock, 2)
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    mask = _recv_exact(sock, 4) if masked else None
    payload = _recv_exact(sock, length) if length else b""
    if mask is not None:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return fin, opcode, payload


class Client:
    """Minimal Home Assistant client (REST plus WebSocket events)."""

    def __init__(self, base_url, token, entities=None, services=None, timeout=10):
        if not base_url:
            raise HomeAssistantError("Home Assistant URL is required")
        if not token:
            raise HomeAssistantError("Home Assistant token is required")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.entities = set(entities) if entities is not None else None
        self.services = set(services) if services is not None else None
        self.timeout = timeout

    @classmethod
    def from_env(cls, environ=None):
        environ = os.environ if environ is None else environ
        return cls(
            base_url=environ.get("HOME_ASSISTANT_URL") or environ.get("HASS_URL", ""),
            token=environ.get("HOME_ASSISTANT_TOKEN") or environ.get("HASS_TOKEN", ""),
            entities=_split_env(environ.get("HOME_ASSISTANT_ENTITIES")),
            services=_split_env(environ.get("HOME_ASSISTANT_SERVICES")),
        )

    # -- allowlist -------------------------------------------------------
    def _check_entity(self, entity_id):
        if self.entities is not None and entity_id not in self.entities:
            raise NotAllowed("entity %r is not allowed" % entity_id)
        return entity_id

    def _check_service(self, domain, service):
        name = "%s.%s" % (domain, service)
        if self.services is not None and name not in self.services:
            raise NotAllowed("service %r is not allowed" % name)
        return name

    # -- REST ------------------------------------------------------------
    def _request(self, method, path, payload=None):
        url = self.base_url + path
        headers = {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise HomeAssistantError("HTTP %s for %s %s" % (exc.code, method, path)) from None
        except urllib.error.URLError as exc:
            raise HomeAssistantError("cannot reach Home Assistant: %s" % (exc.reason,)) from None
        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    def get_states(self):
        """Return entity states, filtered to the allowlist when one is set."""
        states = self._request("GET", "/api/states") or []
        if self.entities is not None:
            return [state for state in states if state.get("entity_id") in self.entities]
        return states

    def get_state(self, entity_id):
        """Return a single entity state."""
        self._check_entity(entity_id)
        return self._request("GET", "/api/states/" + urllib.parse.quote(entity_id))

    def call_service(self, domain, service, data=None, entity_id=None):
        """Call ``domain.service`` with optional service data."""
        self._check_service(domain, service)
        payload = dict(data or {})
        if entity_id is not None:
            self._check_entity(entity_id)
            payload.setdefault("entity_id", entity_id)
        return self._request("POST", "/api/services/%s/%s" % (domain, service), payload)

    # -- WebSocket -------------------------------------------------------
    def _ws_url(self):
        parts = urllib.parse.urlsplit(self.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return urllib.parse.urlunsplit((scheme, parts.netloc, "/api/websocket", "", ""))

    @staticmethod
    def _read_headers(sock):
        buffer = b""
        while not buffer.endswith(b"\r\n\r\n"):
            chunk = sock.recv(1)
            if not chunk:
                break
            buffer += chunk
        return buffer.decode("latin-1", "replace")

    def _open_socket(self):
        parts = urllib.parse.urlsplit(self._ws_url())
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "wss" else 80)
        sock = socket.create_connection((host, port), timeout=self.timeout)
        if parts.scheme == "wss":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%s\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ) % (parts.path or "/", host, port, key)
        sock.sendall(handshake.encode("ascii"))
        response = self._read_headers(sock)
        expected = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        if " 101 " not in response.split("\r\n", 1)[0] or expected not in response:
            sock.close()
            raise HomeAssistantError("Home Assistant WebSocket handshake failed")
        return sock

    @staticmethod
    def _ws_recv(sock):
        while True:
            _fin, opcode, payload = _read_frame(sock)
            if opcode == 0x9:
                sock.sendall(_encode_frame(payload, opcode=0xA))
                continue
            if opcode == 0x8:
                raise HomeAssistantError("WebSocket closed by Home Assistant")
            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))

    def subscribe_states(self, entity_ids=None):
        """Yield ``state_changed`` events from the WebSocket API.

        Events outside the caller's allowlist (and, when given, outside
        ``entity_ids``) are skipped.
        """
        wanted = set(entity_ids) if entity_ids is not None else None
        sock = self._open_socket()
        try:
            greeting = self._ws_recv(sock)
            if greeting.get("type") != "auth_required":
                raise HomeAssistantError("unexpected WebSocket greeting")
            sock.sendall(_encode_frame(json.dumps({
                "type": "auth", "access_token": self._token,
            })))
            if self._ws_recv(sock).get("type") != "auth_ok":
                raise HomeAssistantError("Home Assistant authentication failed")
            sock.sendall(_encode_frame(json.dumps({
                "id": 1, "type": "subscribe_events", "event_type": "state_changed",
            })))
            if not self._ws_recv(sock).get("success"):
                raise HomeAssistantError("could not subscribe to state changes")
            while True:
                message = self._ws_recv(sock)
                if message.get("type") != "event":
                    continue
                event = message.get("event", {})
                data = event.get("data", {})
                entity_id = data.get("entity_id")
                if entity_id is None:
                    entity_id = (data.get("new_state") or {}).get("entity_id")
                if entity_id is None:
                    continue
                try:
                    self._check_entity(entity_id)
                except NotAllowed:
                    continue
                if wanted is not None and entity_id not in wanted:
                    continue
                yield event
        finally:
            sock.close()


def from_env(environ=None):
    """Build a :class:`Client` from ``HOME_ASSISTANT_*`` environment variables."""
    return Client.from_env(environ)
