from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class SpotifyError(Exception):
    """Base exception for Spotify client errors."""


class PremiumRequiredError(SpotifyError):
    """Raised when playback control fails because Spotify Premium is required."""


class SpotifyClient:
    """Client for Spotify Web API."""

    BASE_URL = "https://api.spotify.com/v1"

    def __init__(self, access_token: str) -> None:
        self.access_token = access_token

    def _send_request(self, endpoint: str, method: str = "PUT", params: Optional[dict[str, Any]] = None) -> None:
        url = f"{self.BASE_URL}{endpoint}"
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"

        req = urllib.request.Request(url, method=method)
        req.add_header("Authorization", f"Bearer {self.access_token}")

        try:
            with urllib.request.urlopen(req) as resp:
                pass
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                try:
                    payload = json.loads(exc.read().decode("utf-8"))
                    reason = payload.get("error", {}).get("reason")
                except Exception:
                    reason = None
                if reason == "PREMIUM_REQUIRED":
                    raise PremiumRequiredError("Playback control requires Spotify Premium.") from exc
            raise SpotifyError(f"Spotify API error: {exc.code} {exc.reason}") from exc

    def play(self, device_id: Optional[str] = None) -> None:
        params = {"device_id": device_id} if device_id else None
        self._send_request("/me/player/play", method="PUT", params=params)

    def pause(self, device_id: Optional[str] = None) -> None:
        params = {"device_id": device_id} if device_id else None
        self._send_request("/me/player/pause", method="PUT", params=params)
