"""Tautulli service integration."""

from __future__ import annotations

import hmac
import json
import urllib.parse
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MAX_NOTIFICATION_LOG_LIMIT = 100

#: Largest accepted Tautulli alert webhook body, in bytes.
MAX_ALERT_BODY = 64 * 1024

#: Alert actions the receiver records; anything else is rejected as unsupported.
SUPPORTED_ALERT_ACTIONS = frozenset({
    "play",
    "stop",
    "pause",
    "resume",
    "watched",
    "buffer",
})


class TautulliClient:
    """Client for interacting with a Tautulli server."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 10.0,
        transport: Callable[..., Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        self.transport = transport

    def __repr__(self) -> str:
        return (
            f"TautulliClient(base_url={self.base_url!r}, "
            f"api_key='***', timeout={self.timeout!r})"
        )

    def __str__(self) -> str:
        return f"TautulliClient(base_url={self.base_url!r})"


def notification_log(limit=50, *, base_url="http://127.0.0.1:8181", api_key="", transport=None):
    """Fetch Tautulli notification log with a clamped limit.

    Invalid limits (<= 0 or non-int) are coerced or defaulted, and values
    greater than MAX_NOTIFICATION_LOG_LIMIT (100) are clamped.
    """
    try:
        limit_val = int(limit)
    except (ValueError, TypeError):
        limit_val = 50

    if limit_val <= 0:
        limit_val = 50
    elif limit_val > MAX_NOTIFICATION_LOG_LIMIT:
        limit_val = MAX_NOTIFICATION_LOG_LIMIT

    params = {
        "cmd": "get_notification_log",
        "length": str(limit_val),
    }
    if api_key:
        params["apikey"] = api_key

    query = urlencode(params)
    url = f"{base_url.rstrip('/')}/api/v2?{query}"

    if transport is not None:
        if callable(transport):
            response = transport(url=url, params=params, limit=limit_val)
        elif hasattr(transport, "get"):
            response = transport.get(url, params=params)
        elif hasattr(transport, "request"):
            response = transport.request("GET", url)
        else:
            raise TypeError("unsupported transport")

        if isinstance(response, str):
            return json.loads(response)
        if isinstance(response, bytes):
            return json.loads(response.decode("utf-8"))
        return response

    req = Request(url, headers={"User-Agent": "Forge/1.0"})
    with urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _alert_field(payload, name):
    """Read ``name`` from a mapping or an attribute-style object."""
    if isinstance(payload, dict):
        return payload.get(name)
    return getattr(payload, name, None)


def _alert_text(value, limit=256):
    """Coerce an alert field to a bounded, non-empty string or ``None``."""
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if not value:
        return None
    return value[:limit]


def normalize_alert(payload):
    """Return a normalized alert record, or ``None`` when unsupported.

    Only a bounded, supported Tautulli alert payload is accepted; the result
    never carries playback instructions, so recording an alert cannot trigger
    playback.
    """
    if not isinstance(payload, dict):
        return None
    action = _alert_text(_alert_field(payload, "action"), 32)
    if action is None or action.lower() not in SUPPORTED_ALERT_ACTIONS:
        return None
    record = {"action": action.lower()}
    for name, limit in (
        ("title", 256),
        ("media_type", 64),
        ("user", 128),
        ("player", 128),
        ("session_key", 64),
    ):
        text = _alert_text(_alert_field(payload, name), limit)
        if text is not None:
            record[name] = text
    return record


class TautulliAlertReceiver:
    """Opt-in receiver for Tautulli alert webhooks.

    Disabled unless a non-empty shared secret is configured.  Every accepted
    request must present that secret (compared in constant time) and a bounded,
    supported alert payload; accepted alerts are recorded only.
    """

    def __init__(self, secret=None, *, max_body=MAX_ALERT_BODY):
        if hasattr(secret, "reveal"):
            secret = secret.reveal()
        if secret is not None and not isinstance(secret, str):
            raise TypeError("secret must be a string or None")
        self._secret = secret or ""
        self.max_body = max_body
        self.alerts: list[dict] = []

    @property
    def enabled(self) -> bool:
        return bool(self._secret)

    def verify(self, provided) -> bool:
        """Return True when *provided* matches the configured shared secret."""
        if not self.enabled or not isinstance(provided, str):
            return False
        return hmac.compare_digest(provided, self._secret)

    def receive(self, body_bytes, provided_secret=None):
        """Handle one alert request, returning ``(status, payload)``.

        Disabled receivers answer 404, unauthenticated requests 401, oversized
        bodies 413, malformed bodies 400 and unsupported alerts 422.  Accepted
        alerts are recorded and answered 202; nothing here starts playback.
        """
        if not self.enabled:
            return 404, {"error": "Not found"}
        if not self.verify(provided_secret):
            return 401, {"error": "Unauthorized"}
        if body_bytes is None:
            body_bytes = b""
        if isinstance(body_bytes, str):
            body_bytes = body_bytes.encode("utf-8")
        if len(body_bytes) > self.max_body:
            return 413, {"error": "Body too large"}
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            return 400, {"error": "Invalid JSON"}
        record = normalize_alert(payload)
        if record is None:
            return 422, {"error": "Unsupported alert"}
        self.alerts.append(record)
        return 202, {"recorded": record}


def get_activity():
    """Placeholder for actual implementation."""
    raise NotImplementedError


def activity():
    try:
        data = get_activity()
    except Exception:
        return None
    if not data:
        return None
    result = {}
    viewer = data.get("viewer")
    if isinstance(viewer, dict):
        name = viewer.get("name")
        if name:
            result["viewer"] = name
    elif isinstance(viewer, str):
        result["viewer"] = viewer
    title = data.get("title")
    if title:
        result["title"] = title
    playback_state = data.get("playback-state")
    if playback_state:
        result["playback-state"] = playback_state
    progress = data.get("progress")
    if progress is not None:
        result["progress"] = progress
    return result if result else None
