"""Calibre-Web OPDS client core with strict same-origin book ID extraction.

The transport is injected (no network or socket use here). It must provide
``fetch(url: str) -> bytes``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Protocol
from urllib.parse import urlsplit

_ID_RE = re.compile(r"[1-9][0-9]{0,17}\Z")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_BAD_CHARS_RE = re.compile(r"[\x00-\x20\x7f\\%]")
_ATOM_LINK = "{http://www.w3.org/2005/Atom}link"


class CalibreTransport(Protocol):
    def fetch(self, url: str) -> bytes:
        """Return the response body for ``url``."""


def _origin(parts):
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme)


class CalibreClient:
    def __init__(self, base_url: str, transport: CalibreTransport):
        if transport is None:
            raise ValueError("transport is required")
        parts = urlsplit(base_url)
        if parts.scheme.lower() not in _DEFAULT_PORTS or not parts.hostname:
            raise ValueError("base_url must be an http(s) URL with a host")
        if parts.username is not None or parts.password is not None:
            raise ValueError("base_url must not contain userinfo")
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self._origin = _origin(parts)
        self._prefix = parts.path.rstrip("/")

    def extract_book_id(self, link) -> str:
        """Return the normalized book ID from a same-origin OPDS book link.

        Raises ValueError for anything else.
        """
        if not isinstance(link, str) or not link:
            raise ValueError("link must be a non-empty string")
        if _BAD_CHARS_RE.search(link):
            raise ValueError("link contains forbidden characters")
        try:
            parts = urlsplit(link)
            port = parts.port
        except ValueError as exc:
            raise ValueError("malformed link") from exc
        if parts.query or parts.fragment:
            raise ValueError("link must not carry query or fragment")
        if parts.scheme or parts.netloc:
            if parts.scheme.lower() not in _DEFAULT_PORTS or not parts.netloc:
                raise ValueError("unsupported link scheme or host")
            if parts.username is not None or parts.password is not None:
                raise ValueError("link must not contain userinfo")
            if _origin(parts) != self._origin:
                raise ValueError("cross-origin link rejected")
        path = parts.path
        prefix = self._prefix + "/opds/book/"
        if not path.startswith(prefix):
            raise ValueError("not an OPDS book link")
        segment = path[len(prefix):]
        if segment.endswith("/"):
            segment = segment[:-1]
        if not _ID_RE.match(segment):
            raise ValueError("malformed book id")
        return segment

    def fetch_book_ids(self, path: str = "/opds") -> list:
        """Fetch an OPDS feed via the transport; return IDs of valid links only."""
        if not path.startswith("/"):
            path = "/" + path
        root = ET.fromstring(self.transport.fetch(self.base_url + path))
        ids = []
        for link in root.iter(_ATOM_LINK):
            try:
                book_id = self.extract_book_id(link.get("href"))
            except ValueError:
                continue
            if book_id not in ids:
                ids.append(book_id)
        return ids
