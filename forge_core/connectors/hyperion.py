"""Hyperion ambient lighting connector (JSON-RPC over HTTP)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class Client:
    """Client for Hyperion.ng JSON-RPC API."""

    def __init__(self, base_url: str = "http://127.0.0.1:8090", token: str | None = None, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> Client:
        base_url = os.environ.get("HYPERION_URL", "http://127.0.0.1:8090")
        token = os.environ.get("HYPERION_TOKEN")
        return cls(base_url=base_url, token=token)

    def _request(self, command: str, **kwargs) -> dict:
        url = f"{self.base_url}/json-rpc"
        payload = {"command": command, **kwargs}
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"token {self._token}"

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result
        except urllib.error.HTTPError as err:
            raise RuntimeError(f"Hyperion API HTTP error: {err.code}") from None
        except urllib.error.URLError as err:
            raise RuntimeError("Hyperion connection error") from None

    def server_info(self) -> dict:
        """Fetch Hyperion server info."""
        return self._request("serverinfo")

    def set_component_state(self, component: str, state: bool) -> dict:
        """Enable or disable a component."""
        return self._request(
            "componentstate",
            componentstate={"component": component, "state": state},
        )

    def set_color(self, color: list[int] | tuple[int, int, int], priority: int, duration: int = 0, origin: str = "forge") -> dict:
        """Set solid color (RGB) with priority."""
        return self._request(
            "color",
            color=list(color),
            priority=priority,
            duration=duration,
            origin=origin,
        )

    def set_effect(self, effect_name: str, priority: int, duration: int = 0, origin: str = "forge", args: dict | None = None) -> dict:
        """Set an effect with priority."""
        effect_data: dict = {"name": effect_name}
        if args is not None:
            effect_data["args"] = args
        return self._request(
            "effect",
            effect=effect_data,
            priority=priority,
            duration=duration,
            origin=origin,
        )

    def clear(self, priority: int) -> dict:
        """Clear active color/effect at given priority."""
        return self._request("clear", priority=priority)

    def set_brightness(self, brightness: int) -> dict:
        """Set brightness (0-100)."""
        return self._request("adjustment", adjustment={"brightness": brightness})
