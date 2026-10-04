"""Roku External Control Protocol (ECP) client.

Roku streaming devices expose a small LAN-only HTTP API on port 8060.  This
module wraps the subset the Spectrum TV connector needs: list installed apps,
launch an app, send keypresses, and read the active app.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DEFAULT_PORT = 8060
DEFAULT_TIMEOUT = 5.0


class RokuError(RuntimeError):
    """Raised when a Roku device cannot be reached or replies unexpectedly."""


def _app_dict(node):
    return {
        "id": node.get("id"),
        "type": node.get("type") or node.tag,
        "version": node.get("version"),
        "name": (node.text or "").strip(),
    }


def _parse(text):
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise RokuError(f"invalid XML from Roku: {exc}") from exc


class RokuECP:
    """Client for a Roku's ECP endpoint at ``http://<host>:<port>``."""

    def __init__(self, host, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT, opener=None):
        if not host:
            raise ValueError("host is required")
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.base_url = f"http://{host}:{self.port}"
        self._opener = opener or urllib.request.urlopen

    def _request(self, method, path):
        request = urllib.request.Request(
            self.base_url + path,
            data=b"" if method == "POST" else None,
            method=method,
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise RokuError(f"{method} {path} failed with HTTP {exc.code}") from exc
        except OSError as exc:
            raise RokuError(f"{method} {path} failed: {exc}") from exc

    def query_apps(self):
        """Return the installed apps as a list of ``{id, name, type, version}``."""
        _, text = self._request("GET", "/query/apps")
        return [_app_dict(node) for node in _parse(text).findall("app")]

    def launch(self, app_id):
        """Launch the app with the given id; returns ``True`` on success."""
        self._request("POST", "/launch/" + urllib.parse.quote(str(app_id), safe=""))
        return True

    def keypress(self, key, count=1):
        """Send ``key`` (for example ``Home`` or ``Select``) ``count`` times."""
        if not key:
            raise ValueError("key is required")
        if count < 1:
            raise ValueError("count must be at least 1")
        path = "/keypress/" + urllib.parse.quote(str(key), safe="")
        for _ in range(count):
            self._request("POST", path)
        return True

    def active_app(self):
        """Return the foreground app, or ``None`` if Roku reports no child."""
        _, text = self._request("GET", "/query/active-app")
        for node in _parse(text):
            return _app_dict(node)
        return None
