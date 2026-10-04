"""Framework-neutral HTTP endpoints for Forge presets and static assets."""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlsplit

from forge_core.layout import PresetStore, normalize_preset, validate_preset
from forge_core.services.config import validate_base_url
from forge_core.services.http import ServiceClient


_BODY_LIMIT = 256 * 1024
_ABS_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_ABS_AUDIO_PREFIX = "/forge/api/audiobookshelf/audio/"
# Only these upstream response headers are relayed to the browser; anything
# else from the upstream server stays server-side.
_ABS_STREAM_HEADERS = frozenset({
    "content-type", "content-length", "content-range", "accept-ranges",
    "cache-control", "etag", "last-modified",
})


def _field(value, name, default=None):
    """Read ``name`` from a mapping or an attribute-style object."""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)
_ID_PATTERN = re.compile(r"[a-z0-9-]+\Z")
_ITEM_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}


_IMAGE_PREFIX = "/forge/api/image/"
_FILE_PREFIX = "/forge/api/file/"
_PROXY_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_PROXY_HEADERS = frozenset({
    "content-type", "content-length", "content-range", "accept-ranges",
    "cache-control", "etag", "last-modified",
})
_PROXY_CHUNK = 64 * 1024
_PROXY_LIMIT = 64 * 1024 * 1024

_COVER_PREFIX = "/forge/api/audiobookshelf/cover/"
_COVER_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_COVER_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"})
_COVER_LIMIT = 5 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_COVER_OPENER = urllib.request.build_opener(_NoRedirect)


def render_family_page(greeting, tiles):
    """Render the /family/ dashboard as standalone UTF-8 HTML.

    ``greeting`` is already-normalized user text (or a safe fallback) and
    ``tiles`` is an iterable of ``{name, connector, status, detail}`` mappings.
    Every value is escaped so connector output can never inject markup, and
    each tile still renders when its connector is offline or unconfigured.
    """
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<title>Family dashboard</title></head><body>",
        "<h1>Family dashboard</h1>",
        f"<p class=\"greeting\">Hello, {html.escape(str(greeting))}</p>",
        "<ul class=\"tiles\">",
    ]
    for tile in tiles or ():
        name = html.escape(str(tile.get("name", "")))
        connector = html.escape(str(tile.get("connector", "")))
        status = html.escape(str(tile.get("status", "unavailable")))
        detail = html.escape(str(tile.get("detail", "")))
        parts.append(
            f"<li class=\"tile\" data-connector=\"{connector}\" data-state=\"{status}\">"
            f"<h2>{name}</h2>"
            f"<p class=\"status\">{status}</p>"
            f"<p class=\"detail\">{detail}</p></li>"
        )
    parts.append("</ul></body></html>")
    return "".join(parts).encode("utf-8")


class ForgeAPI:
    def __init__(
        self,
        store: PresetStore,
        registry: dict,
        can_write=lambda request: False,
        audiobookshelf_url: str | None = None,
        audiobookshelf_token=None,
        cover_provider=None,
        can_view=lambda request: True,
        audiobookshelf=None,
        tautulli_alerts=None,
        nextcloud_summary=None,
        proxy=None,
    ):
        self.store = store
        self.registry = registry
        self.can_write = can_write
        # Opt-in Tautulli alert receiver; disabled (None) leaves the
        # notification-log endpoints and everything else untouched.
        self.tautulli_alerts = tautulli_alerts
        # Opt-in cover provider: callable(item_id) -> (content_type, bytes) or None.
        # When set it takes precedence over the URL proxy and is gated by can_view.
        self.cover_provider = cover_provider
        self.can_view = can_view
        # Opt-in same-origin audio backend exposing item_access(request, item_id),
        # session(request, item_id, session_id) and stream(item_id, session_id, range).
        self.audiobookshelf = audiobookshelf
        # Opt-in same-origin Nextcloud summary provider: callable(request) -> dict.
        # It must return a bounded, secret-free payload; the default is unavailable.
        self.nextcloud_summary = nextcloud_summary or (lambda request: {
            "authorized": False, "status": "unavailable", "reason": "unconfigured",
            "events": [], "files": [], "refresh_seconds": 60, "min_refresh_seconds": 30,
        })
        # Opt-in same-origin proxy backend exposing fetch(kind, resource_id, range_header)
        # -> (status, headers, body) or None. Gated by can_view on every request.
        self.proxy = proxy
        self.audiobookshelf_url = (
            validate_base_url(audiobookshelf_url) if audiobookshelf_url else None
        )
        if hasattr(audiobookshelf_token, "reveal"):
            audiobookshelf_token = audiobookshelf_token.reveal()
        self._audiobookshelf_token = audiobookshelf_token

    def _cover(self, item_id: str) -> tuple[int, dict, bytes]:
        """Proxy a cover from the configured server; credentials stay server-side."""
        if not self.audiobookshelf_url or not _COVER_ID_PATTERN.fullmatch(item_id):
            return self._error(404, "Not found")
        headers = {"Accept": "image/*"}
        if self._audiobookshelf_token:
            headers["Authorization"] = f"Bearer {self._audiobookshelf_token}"
        url = f"{self.audiobookshelf_url.rstrip('/')}/api/items/{item_id}/cover"
        try:
            with _COVER_OPENER.open(urllib.request.Request(url, headers=headers), timeout=10) as response:
                content_type = response.headers.get_content_type()
                data = response.read(_COVER_LIMIT + 1)
        except urllib.error.HTTPError as exc:
            exc.close()
            return self._error(404 if exc.code == 404 else 502, "Cover unavailable")
        except (OSError, ValueError):
            return self._error(502, "Cover unavailable")
        if content_type not in _COVER_TYPES or len(data) > _COVER_LIMIT:
            return self._error(502, "Cover unavailable")
        return 200, {
            "Content-Type": content_type,
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        }, data

    def _audiobookshelf_audio(self, remainder: str, request) -> tuple[int, dict, bytes]:
        """Relay audio for ``<item_id>/<session_id>``; access is re-checked on every request.

        The backend decides item access (None -> 404, falsy -> 403) and session
        validity (None -> 404, unauthorized or mismatched item -> 403). Only a
        safe allow-list of upstream headers reaches the browser.
        """
        parts = remainder.split("/")
        if len(parts) != 2 or not all(_ABS_ID_PATTERN.fullmatch(part) for part in parts):
            return self._error(404, "Not found")
        item_id, session_id = parts
        backend = self.audiobookshelf
        access = backend.item_access(request, item_id)
        if access is None:
            return self._error(404, "Not found")
        if not access:
            return self._error(403, "Forbidden")
        session = backend.session(request, item_id, session_id)
        if session is None:
            return self._error(404, "Not found")
        if not _field(session, "authorized", False):
            return self._error(403, "Forbidden")
        session_item = _field(session, "libraryItemId", item_id)
        if session_item is not None and session_item != item_id:
            return self._error(403, "Forbidden")
        request_headers = getattr(request, "headers", None)
        range_header = request_headers.get("Range") if request_headers is not None else None
        try:
            status, upstream_headers, body = backend.stream(item_id, session_id, range_header)
        except (OSError, ValueError):
            return self._error(502, "Audio unavailable")
        headers = {name: value for name, value in dict(upstream_headers or {}).items()
                   if name.lower() in _ABS_STREAM_HEADERS}
        headers.setdefault("Cache-Control", "no-store")
        headers["X-Content-Type-Options"] = "nosniff"
        return status, headers, body

    def _proxy(self, kind: str, resource_id: str, request) -> tuple[int, dict, bytes]:
        """Stream an allowed image or file through the shared proxy helper.

        The mounting application's viewer policy is enforced on every request;
        only a safe allow-list of upstream headers is relayed and the upstream
        URL and credentials never reach the browser.
        """
        if self.proxy is None:
            return self._error(404, "Not found")
        if not self.can_view(request):
            return self._error(403, "Forbidden")
        if not _PROXY_ID_PATTERN.fullmatch(resource_id):
            return self._error(404, "Not found")
        request_headers = getattr(request, "headers", None)
        range_header = request_headers.get("Range") if request_headers is not None else None
        try:
            result = self.proxy.fetch(kind, resource_id, range_header)
        except (OSError, ValueError):
            return self._error(502, "Unavailable")
        if result is None:
            return self._error(404, "Not found")
        status, upstream_headers, body = result
        headers = {name: value for name, value in dict(upstream_headers or {}).items()
                   if name.lower() in _PROXY_HEADERS}
        headers.setdefault("Cache-Control", "no-store")
        headers["X-Content-Type-Options"] = "nosniff"
        return status, headers, body

    @staticmethod
    def _json(status: int, value: object, *, api: bool = True) -> tuple[int, dict, bytes]:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if api:
            headers["Cache-Control"] = "no-store"
        return status, headers, payload

    @classmethod
    def _error(cls, status: int, message: str, *, api: bool = True) -> tuple[int, dict, bytes]:
        return cls._json(status, {"error": message}, api=api)

    @staticmethod
    def _static(filename: str) -> tuple[int, dict, bytes]:
        # Files under the vendored static directory (including static/theme-kit/) are public;
        # empty, dot and backslash segments are refused, and the resolved path must stay inside.
        parts = filename.split("/") if filename else []
        if not parts or "\\" in filename or any(part in {"", ".", ".."} for part in parts):
            return ForgeAPI._error(404, "Not found", api=False)
        candidate = _STATIC_DIRECTORY.joinpath(*parts)
        root = _STATIC_DIRECTORY.resolve()
        if not candidate.is_file() or root not in candidate.resolve().parents:
            return ForgeAPI._error(404, "Not found", api=False)
        content_type = _CONTENT_TYPES.get(candidate.suffix.lower(), "application/octet-stream")
        return 200, {"Content-Type": content_type}, candidate.read_bytes()

    def handle(
        self, method: str, path: str, body_bytes: bytes, request=None
    ) -> tuple[int, dict, bytes]:
        raw_path = urlsplit(path).path
        route = unquote(raw_path)
        is_api = route.startswith("/forge/api/") or route == "/forge/api"
        if len(body_bytes) > _BODY_LIMIT:
            return self._error(413, "Body too large", api=is_api)

        if route == "/forge/designer" and method == "GET":
            return self._static("designer.html")
        if route.startswith("/forge/static/") and method == "GET":
            return self._static(route[len("/forge/static/"):])

        if route.startswith(_IMAGE_PREFIX) and method in {"GET", "HEAD"}:
            return self._proxy("image", route[len(_IMAGE_PREFIX):], request)
        if route.startswith(_FILE_PREFIX) and method in {"GET", "HEAD"}:
            return self._proxy("file", route[len(_FILE_PREFIX):], request)

        if route.startswith(_COVER_PREFIX) and method == "GET":
            item_id = route[len(_COVER_PREFIX):]
            if self.cover_provider is None:
                return self._cover(item_id)
            if not self.can_view(request):
                return self._error(403, "Forbidden")
            if not _ITEM_ID_PATTERN.fullmatch(item_id):
                return self._error(404, "Not found")
            cover = self.cover_provider(item_id)
            if not cover:
                return self._error(404, "Not found")
            content_type, data = cover
            return 200, {"Content-Type": content_type, "Cache-Control": "no-store"}, data

        if route == "/forge/api/tautulli/alerts" and method == "POST":
            receiver = self.tautulli_alerts
            if receiver is None or not getattr(receiver, "enabled", False):
                return self._error(404, "Not found")
            headers = getattr(request, "headers", None)
            provided = headers.get("X-Tautulli-Secret") if headers is not None else None
            status, payload = receiver.receive(body_bytes, provided)
            return self._json(status, payload)

        if route == "/forge/api/registry" and method == "GET":
            return self._json(200, self.registry)
        if route == "/forge/api/nextcloud/summary" and method == "GET":
            if not self.can_view(request):
                return self._error(403, "Forbidden")
            return self._json(200, self.nextcloud_summary(request))
        if route == "/forge/api/presets" and method == "GET":
            return self._json(200, self.store.list())
        if route == "/forge/api/active" and method == "GET":
            return self._json(200, {"id": self.store.get_active()})

        if (
            route.startswith(_ABS_AUDIO_PREFIX)
            and method in {"GET", "HEAD"}
            and self.audiobookshelf is not None
            and getattr(self.audiobookshelf, "enabled", False)
        ):
            return self._audiobookshelf_audio(route[len(_ABS_AUDIO_PREFIX):], request)

        preset_prefix = "/forge/api/presets/"
        preset_id = route[len(preset_prefix):] if route.startswith(preset_prefix) else None
        if preset_id is not None and not _ID_PATTERN.fullmatch(preset_id):
            return self._error(404, "Not found")

        if preset_id is not None and method == "GET":
            try:
                return self._json(200, self.store.get(preset_id))
            except KeyError:
                return self._error(404, "Not found")
            except (OSError, ValueError) as exc:
                return self._json(500, {"errors": [str(exc) or "Preset is unreadable"]})

        if method in {"PUT", "DELETE"} and (
            (preset_id is not None and method in {"PUT", "DELETE"})
            or (route == "/forge/api/active" and method == "PUT")
        ):
            if not self.can_write(request):
                return self._error(403, "Forbidden")

            if method == "DELETE":
                try:
                    self.store.delete(preset_id)
                except KeyError:
                    return self._error(404, "Not found")
                return 204, {"Cache-Control": "no-store"}, b""

            try:
                data = json.loads(body_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
                return self._error(400, "Invalid JSON")
            if not isinstance(data, dict):
                return self._json(400, {"errors": ["Body must be a JSON object"]})

            if route == "/forge/api/active":
                active_id = data.get("id")
                if not isinstance(active_id, str) or not _ID_PATTERN.fullmatch(active_id):
                    return self._json(400, {"errors": ["Invalid preset id"]})
                try:
                    self.store.set_active(active_id)
                except KeyError:
                    return self._error(404, "Not found")
                except ValueError as exc:
                    return self._json(400, {"errors": [str(exc)]})
                return self._json(200, {"id": active_id})

            if data.get("id") != preset_id:
                return self._json(400, {"errors": ["Preset id must match the URL"]})
            try:
                normalized = normalize_preset(data, self.registry)
                errors = validate_preset(normalized, self.registry)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                return self._json(400, {"errors": [str(exc) or "Invalid preset"]})
            if errors:
                return self._json(400, {"errors": errors})
            try:
                saved = self.store.save(normalized)
            except (ValueError, TypeError) as exc:
                return self._json(400, {"errors": [str(exc) or "Invalid preset"]})
            return self._json(200, saved)

        return self._error(404, "Not found", api=is_api)


def flask_blueprint(api: ForgeAPI, url_prefix: str = "/forge"):
    """Create the optional Flask adapter without requiring Flask at import time."""
    from flask import Blueprint, request

    blueprint = Blueprint("forge", __name__, url_prefix=url_prefix)
    methods = ["GET", "PUT", "DELETE", "POST", "PATCH", "HEAD", "OPTIONS"]

    @blueprint.route("", methods=methods, provide_automatic_options=False)
    @blueprint.route("/<path:remainder>", methods=methods, provide_automatic_options=False)
    def dispatch(remainder=""):
        from flask import Response

        # Reading at most one byte beyond the limit avoids buffering an
        # unbounded request body in the optional adapter.
        body = request.stream.read(_BODY_LIMIT + 1)
        status, headers, payload = api.handle(request.method, request.path, body, request)
        return Response(payload, status=status, headers=headers)

    return blueprint


def serve_stdlib(handler, api: ForgeAPI) -> bool:
    """Handle a Forge request from a BaseHTTPRequestHandler."""
    if not handler.path.startswith("/forge"):
        return False

    length_header = handler.headers.get("Content-Length", "0")
    try:
        length = int(length_header)
        if length < 0:
            raise ValueError("negative Content-Length")
    except ValueError:
        status, headers, payload = api._error(400, "Invalid Content-Length")
        handler.close_connection = True
    else:
        body = handler.rfile.read(min(length, _BODY_LIMIT + 1))
        if length > len(body):
            handler.close_connection = True
        status, headers, payload = api.handle(handler.command, handler.path, body, handler)

    handler.send_response(status)
    for name, value in headers.items():
        handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    if handler.command != "HEAD" and payload:
        handler.wfile.write(payload)
    return True
