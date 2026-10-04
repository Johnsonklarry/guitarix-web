"""TrueNAS home-server connector.

Reads server settings and performance statistics through the TrueNAS REST
API (v2.0) using only the Python standard library. The API key is supplied by
the caller from server-side configuration; it is never stored or logged.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping

API_PREFIX = "/api/v2.0"
DEFAULT_TIMEOUT = 10.0
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class TrueNASError(RuntimeError):
    """Raised when the TrueNAS server is unreachable or returns an error."""


class TrueNASClient:
    """Small, read-mostly client for the TrueNAS REST API v2.0."""

    def __init__(self, base_url, api_key, *, timeout=DEFAULT_TIMEOUT, opener=None):
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be a non-empty string")
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be a positive number")
        self.base_url = base_url.strip().rstrip("/")
        self.timeout = float(timeout)
        self._api_key = api_key
        self._open = opener or _OPENER.open

    def _request(self, method, path, payload=None):
        url = f"{self.base_url}{API_PREFIX}{path}"
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._open(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise TrueNASError(f"TrueNAS returned HTTP {exc.code} for {method} {path}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise TrueNASError(f"TrueNAS request failed for {method} {path}") from exc
        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrueNASError(f"TrueNAS returned malformed JSON for {method} {path}") from exc

    def system_info(self):
        """Return the server's ``system/info`` payload."""
        return self._request("GET", "/system/info")

    def settings(self):
        """Return the server's general configuration."""
        return self._request("GET", "/system/general")

    def update_settings(self, changes):
        """Apply ``changes`` to the server's general configuration."""
        if not isinstance(changes, Mapping) or not changes:
            raise ValueError("changes must be a non-empty mapping")
        return self._request("PUT", "/system/general", dict(changes))

    def performance(self):
        """Return the live performance subset shown on the dashboard."""
        info = self.system_info()
        if not isinstance(info, Mapping):
            raise TrueNASError("Unexpected TrueNAS system/info response")
        return {
            "hostname": info.get("hostname"),
            "version": info.get("version"),
            "uptime_seconds": info.get("uptime_seconds"),
            "loadavg": info.get("loadavg"),
            "cores": info.get("cores"),
            "physmem": info.get("physmem"),
        }


def read_ups_vars(host, ups="ups", port=3493, timeout=DEFAULT_TIMEOUT):
    """Return the NUT variables of one UPS as a dict of strings."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(f"LIST VAR {ups}\n".encode("utf-8"))
            reader = sock.makefile("r", encoding="utf-8", newline="\n")
            values = {}
            for line in reader:
                line = line.strip()
                if line.startswith("ERR"):
                    raise TrueNASError(f"NUT server error: {line}")
                if line.startswith("END LIST VAR"):
                    return values
                if line.startswith("VAR "):
                    parts = line.split(" ", 3)
                    if len(parts) == 4:
                        values[parts[2]] = parts[3].strip('"')
    except (OSError, ValueError) as exc:
        raise TrueNASError("NUT request failed") from exc
    raise TrueNASError("NUT response was truncated")


def ups_badge(values, low_runtime=300):
    """Summarise NUT variables as an outage badge for dashboards."""
    try:
        charge = float(values["battery.charge"])
        runtime = float(values["battery.runtime"])
    except (KeyError, TypeError, ValueError):
        return {"state": "unknown", "charge": None, "runtime_seconds": None}
    status = str(values.get("ups.status", "")).split()
    if "OB" in status:
        critical = "LB" in status or runtime <= low_runtime
        state = "critical" if critical else "on_battery"
    else:
        state = "online"
    return {"state": state, "charge": charge, "runtime_seconds": runtime}
