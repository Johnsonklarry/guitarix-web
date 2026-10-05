"""Minimal Plex Media Server client with normalized now-playing sessions and transport skeleton.

Part 1 of the Plex connector work: ``PlexClient.sessions()`` reaches the
server's ``/status/sessions`` endpoint and flattens each raw ``Metadata``
entry into a provider-neutral "now playing" dict so dashboards do not have
to know Plex's field names.

stdlib only, matching the rest of forge_core.

Plex client and discovery helpers.

``ServiceClient`` is a tiny stdlib-only HTTP helper: it builds a
:class:`urllib.request.Request`, hands it to an injectable *transport*
callable, and always passes an explicit timeout. The default transport is
:func:`urllib.request.urlopen`, so tests can substitute a fake transport and
perform no real network access.

Plex control helpers: command allowlist and identifier validators.

Pure, dependency-free validators used to guard Plex control requests before
they reach the Plex API. Every validator raises ``ValueError`` for malformed
input and returns the validated value on success.

Plex media server integration utilities.
"""

from __future__ import annotations

import hmac
import json
import re
import urllib.error
from urllib.error import HTTPError, URLError
import urllib.parse
from urllib.parse import quote_plus, urljoin
import urllib.request
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT = 10.0
PLEX_DEFAULT_URL = "http://TRUENAS100:32400"

#: Commands the dashboard is allowed to forward to a Plex client.
ALLOWED_COMMANDS = frozenset((
    "play",
    "pause",
    "stop",
    "skip_next",
    "skip_previous",
))

#: Plex webhook events the activity parser understands.
SUPPORTED_EVENTS = frozenset((
    "media.play",
    "media.pause",
    "media.resume",
    "media.stop",
    "media.scrobble",
))

#: Slugs may contain ASCII letters, digits, ``.``, ``_`` and ``-`` and must
#: start with a letter or digit.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

CLIENT_ID_MAX_LENGTH = 128
MEDIA_KEY_MAX_LENGTH = 256
QUEUE_ID_MAX_LENGTH = 128


def _validate_identifier(value, label, max_length):
    """Validate a single identifier, raising ``ValueError`` when malformed."""
    if not isinstance(value, str):
        raise ValueError("%s must be a string" % (label,))
    if not value:
        raise ValueError("%s must not be empty" % (label,))
    if len(value) > max_length:
        raise ValueError(
            "%s exceeds maximum length of %d characters" % (label, max_length)
        )
    if _IDENTIFIER_RE.match(value) is None:
        raise ValueError("%s contains invalid characters" % (label,))
    return value


def validate_client_id(client_id):
    """Return ``client_id`` if it is a well-formed Plex client identifier."""
    return _validate_identifier(client_id, "client id", CLIENT_ID_MAX_LENGTH)


def validate_media_key(media_key):
    """Return ``media_key`` if it is a well-formed Plex media key."""
    return _validate_identifier(media_key, "media key", MEDIA_KEY_MAX_LENGTH)


def validate_queue_id(queue_id):
    """Return ``queue_id`` if it is a well-formed Plex play queue identifier."""
    return _validate_identifier(queue_id, "queue id", QUEUE_ID_MAX_LENGTH)


def validate_command(command):
    """Return ``command`` if it is in :data:`ALLOWED_COMMANDS`."""
    if not isinstance(command, str) or command not in ALLOWED_COMMANDS:
        raise ValueError("unsupported command: %r" % (command,))
    return command


def create_play_queue(keys, confirm=False, transport=None):
    """Create a play queue from media keys against a transport.

    Requires confirm=True to take action. Validates keys is a non-empty list of
    valid media key strings or ints, and returns the queue id from the transport.
    """
    if not confirm:
        raise ValueError("confirm=True is required to create a play queue")
    if not isinstance(keys, list) or not keys:
        raise ValueError("keys must be a non-empty list")
    for key in keys:
        if not (isinstance(key, (int, str)) and str(key).strip()):
            raise ValueError(f"invalid media key: {key!r}")

    if transport is None:
        raise ValueError("transport is required")

    if hasattr(transport, "create_play_queue"):
        return transport.create_play_queue(keys=keys)
    if hasattr(transport, "post"):
        response = transport.post("/playQueues", json={"keys": keys})
        if isinstance(response, dict):
            return response.get("playQueueID") or response.get("id")
        return response
    if callable(transport):
        return transport(keys)
    raise TypeError("unsupported transport type")


class PlexError(RuntimeError):
    """Raised when the Plex server is unreachable or returns invalid JSON."""


class PlexTransportError(PlexError):
    """Raised when the injected transport fails (wraps the original exception)."""


class PlexConfigError(PlexError):
    """Raised when the Plex client is misconfigured."""


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_activity_event(body):
    """Parse a Plex webhook payload into a normalized activity event dict.

    Accepts ``bytes``, ``str`` or an already-decoded JSON payload. Returns
    ``None`` for malformed JSON, non-object payloads, or events outside
    :data:`SUPPORTED_EVENTS`. This function is pure: it never touches a
    transport, :class:`PlexClient` or :data:`ALLOWED_COMMANDS`, so it can
    never trigger playback, and it never raises on bad input.
    """
    if isinstance(body, (bytes, bytearray)):
        try:
            body = bytes(body).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (ValueError, RecursionError):
            return None
    if not isinstance(body, dict):
        return None
    event = body.get("event")
    if not isinstance(event, str) or event not in SUPPORTED_EVENTS:
        return None
    account = body.get("Account")
    player = body.get("Player")
    metadata = body.get("Metadata")
    if not isinstance(account, dict):
        account = {}
    if not isinstance(player, dict):
        player = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "event": event,
        "user": account.get("title"),
        "player": player.get("title"),
        "media_type": metadata.get("type"),
        "title": metadata.get("title"),
        "rating_key": metadata.get("ratingKey"),
    }


class PlexWebhookReceiver:
    """Receive Plex webhook events and expose the latest parsed activity.

    The receiver is inert unless constructed with a non-empty ``secret``: the
    :attr:`enabled` property is ``False`` for any other value, which makes the
    ForgeAPI route answer 404. Every request must present a matching secret
    (compared with :func:`hmac.compare_digest`); mismatches are rejected before
    the body is parsed. The receiver only ever parses events through
    :func:`parse_activity_event` and never forwards a playback command.
    """

    def __init__(self, secret, sessions_provider=None):
        self.secret = secret
        self.sessions_provider = sessions_provider
        self._last_event = None

    @property
    def enabled(self):
        """True only when a non-empty string secret was configured."""
        return isinstance(self.secret, str) and bool(self.secret)

    def receive(self, body, secret):
        """Validate the secret and parse ``body`` into a normalized event.

        Returns ``(403, {"error": "Forbidden"})`` when the secret is missing or
        does not match, ``(202, {"accepted": True, "event": event})`` for a
        supported event, and ``(200, {"accepted": False})`` for unsupported or
        malformed bodies.
        """
        if not self.enabled:
            return 403, {"error": "Forbidden"}
        if not isinstance(secret, str) or not hmac.compare_digest(secret, self.secret):
            return 403, {"error": "Forbidden"}
        event = parse_activity_event(body)
        if event is None:
            return 200, {"accepted": False}
        self._last_event = event
        return 202, {"accepted": True, "event": event["event"]}

    def latest_activity(self):
        """Return the last parsed event, falling back to a bounded session poll.

        When no webhook event has been received (for example because the server
        has no Plex Pass) and a ``sessions_provider`` was supplied, the provider
        is called and its result returned. A :class:`PlexError` from the
        provider yields ``[]`` so a refresh never raises.
        """
        if self._last_event is not None:
            return self._last_event
        if self.sessions_provider is None:
            return None
        try:
            return self.sessions_provider()
        except PlexError:
            return []


class ServiceClient:
    """Minimal HTTP client with a configurable URL and injectable transport."""

    def __init__(self, base_url, *, transport=None, timeout=DEFAULT_TIMEOUT):
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = str(base_url).rstrip("/")
        self.timeout = timeout
        self.transport = transport or urlopen

    def request(self, path, *, method="GET", headers=None, data=None):
        """Perform one request, always passing an explicit timeout."""
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        request = Request(url, data=data, headers=dict(headers or {}), method=method)
        return self.transport(request, timeout=self.timeout)

    def get(self, path, *, headers=None):
        return self.request(path, method="GET", headers=headers)


class PlexClient(ServiceClient):
    """Talk to a Plex Media Server over its HTTP API."""

    def __init__(
        self,
        base_url: str = PLEX_DEFAULT_URL,
        token: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        opener=None,
        transport=None,
        default_limit: int = 50,
        max_limit: int = 100,
    ):
        chosen_transport = transport if transport is not None else opener
        super().__init__(base_url=base_url, transport=chosen_transport, timeout=timeout)
        self.token = token
        self._opener = self.transport
        self.default_limit = default_limit
        self.max_limit = max_limit

    def __repr__(self):
        return "PlexClient(base_url=%r, timeout=%r)" % (self.base_url, self.timeout)

    __str__ = __repr__

    def sections(self):
        """Return the raw response for ``/library/sections``."""
        return self.get("/library/sections")

    def _get(self, path, params=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Accept": "application/json"}
        if self.token:
            headers["X-Plex-Token"] = self.token
        request = urllib.request.Request(url, headers=headers)
        try:
            response = self._opener(request, timeout=self.timeout)
            with response:
                payload = response.read()
        except Exception as exc:
            raise PlexTransportError(
                f"Plex request failed: {type(exc).__name__}: {exc}"
            ) from exc
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PlexError("Plex returned invalid JSON") from exc

    def _request(self, path: str) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
        }
        if self.token:
            headers["X-Plex-Token"] = self.token
        req = Request(url, headers=headers)
        with self._opener(req, timeout=self.timeout) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8"))

    def sessions(self):
        """Return the active sessions as normalized now-playing dicts.

        Each entry carries ``session_id``, ``user``, ``player``, ``state``,
        ``media_type``, ``title``, ``grandparent_title``, ``parent_title``,
        ``season``, ``episode``, ``duration_ms``, ``view_offset_ms`` and
        ``progress`` (a 0.0-1.0 float).
        """
        container = self._get("/status/sessions").get("MediaContainer") or {}
        return [self._normalize(item) for item in container.get("Metadata") or []]

    @staticmethod
    def _normalize(item):
        player = item.get("Player") or {}
        user = item.get("User") or {}
        session = item.get("Session") or {}
        duration = _as_int(item.get("duration"))
        offset = _as_int(item.get("viewOffset")) or 0
        if duration:
            progress = max(0.0, min(1.0, offset / duration))
        else:
            progress = 0.0
        return {
            "session_id": session.get("id") or item.get("sessionKey"),
            "user": user.get("title"),
            "player": player.get("title"),
            "state": player.get("state") or "unknown",
            "media_type": item.get("type"),
            "title": item.get("title"),
            "grandparent_title": item.get("grandparentTitle"),
            "parent_title": item.get("parentTitle"),
            "season": _as_int(item.get("parentIndex")),
            "episode": _as_int(item.get("index")),
            "duration_ms": duration,
            "view_offset_ms": offset,
            "progress": progress,
        }

    def _normalize_metadata(self, item: dict) -> dict:
        return {
            "rating_key": item.get("ratingKey"),
            "title": item.get("title"),
            "type": item.get("type"),
            "year": item.get("year"),
            "summary": item.get("summary"),
            "added_at": item.get("addedAt"),
            "thumb": item.get("thumb"),
        }

    def _resolve_limit(self, limit: int | None) -> int:
        if limit is None:
            return self.default_limit
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise TypeError("limit must be an integer")
        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        if limit > self.max_limit:
            raise ValueError(f"limit cannot exceed {self.max_limit}")
        return limit

    def search(self, query: str, limit: int | None = None) -> list[dict]:
        limit_val = self._resolve_limit(limit)
        encoded_query = quote_plus(query)
        path = f"/search?query={encoded_query}&X-Plex-Container-Size={limit_val}"
        data = self._request(path)
        container = data.get("MediaContainer", {})
        items = container.get("Metadata", [])
        return [self._normalize_metadata(it) for it in items[:limit_val]]

    def recently_added(self, limit: int | None = None) -> list[dict]:
        limit_val = self._resolve_limit(limit)
        path = f"/library/recentlyAdded?X-Plex-Container-Size={limit_val}"
        data = self._request(path)
        container = data.get("MediaContainer", {})
        items = container.get("Metadata", [])
        return [self._normalize_metadata(it) for it in items[:limit_val]]
