"""Marantz AV receiver connector (HTTP / status and control)."""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

__all__ = ["Client", "MarantzError", "from_env"]


class MarantzError(RuntimeError):
    """Raised for Marantz AVR communication errors."""


class Client:
    """Client for Marantz / Denon AVR HTTP control interface."""

    def __init__(self, host: str = "127.0.0.1", port: int = 80, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.base_url = f"http://{self.host}:{self.port}"

    @classmethod
    def from_env(cls, env: dict | None = None) -> Client:
        environ = os.environ if env is None else env
        host = environ.get("MARANTZ_HOST", "127.0.0.1")
        port_str = environ.get("MARANTZ_PORT", "80")
        try:
            port = int(port_str)
        except ValueError:
            port = 80
        return cls(host=host, port=port)

    def _command(self, cmd: str) -> None:
        url = f"{self.base_url}/goform/formiPhoneAppDirect.xml?{urllib.parse.quote(cmd, safe='')}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            raise MarantzError(f"Marantz command failed with HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise MarantzError("Marantz AVR is unreachable") from None

    def status(self) -> dict:
        """Read AVR status XML via /goform/formMainZone_MainZoneXml.xml."""
        url = f"{self.base_url}/goform/formMainZone_MainZoneXml.xml"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            raise MarantzError(f"Marantz status failed with HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise MarantzError("Marantz AVR is unreachable") from None

        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise MarantzError("Invalid XML response from Marantz AVR") from exc

        def _val(tag: str) -> str | None:
            elem = root.find(tag)
            return elem.text.strip() if elem is not None and elem.text else None

        power_val = _val("Power/value") or _val("ZonePower/value")
        power = (power_val.upper() == "ON") if power_val else False

        input_val = _val("InputFuncSelect/value")

        mute_val = _val("Mute/value")
        mute = (mute_val.upper() == "ON") if mute_val else False

        vol_val = _val("MasterVolume/value")
        volume = None
        if vol_val is not None:
            try:
                volume = float(vol_val)
            except ValueError:
                volume = None

        return {
            "power": power,
            "input": input_val,
            "mute": mute,
            "volume": volume,
        }

    def power_on(self) -> None:
        """Turn on the receiver."""
        self._command("PWON")

    def power_off(self) -> None:
        """Turn off (standby) the receiver."""
        self._command("PWSTANDBY")

    def select_input(self, source: str) -> None:
        """Select input source (e.g. 'SAT/CBL', 'TV', 'BD', 'MPLAY', 'GAME')."""
        self._command(f"SI{source.upper()}")

    def set_volume(self, level: int | float) -> None:
        """Set main volume level (0-99)."""
        rounded = round(float(level))
        self._command(f"MV{rounded:02d}")

    def volume_set(self, level: int | float) -> None:
        """Set volume level clamped to 0-100."""
        clamped = max(0.0, min(100.0, float(level)))
        rounded = round(clamped)
        self._command(f"MV{rounded:02d}")

    def volume_step(self, delta: int | float) -> None:
        """Step volume level by delta, clamped to 0-100."""
        current = self.status().get("volume")
        base = float(current) if current is not None else 0.0
        self.volume_set(base + float(delta))

    def volume_up(self) -> None:
        """Increase volume by 1 step."""
        self._command("MVUP")

    def volume_down(self) -> None:
        """Decrease volume by 1 step."""
        self._command("MVDOWN")

    def set_mute(self, mute: bool) -> None:
        """Mute or unmute receiver."""
        self._command("MUON" if mute else "MUOFF")

    def mute(self) -> None:
        """Mute the receiver."""
        self.set_mute(True)

    def unmute(self) -> None:
        """Unmute the receiver."""
        self.set_mute(False)

    def mute_status(self) -> bool:
        """Return mute status."""
        return bool(self.status().get("mute"))

    def mute_toggle(self) -> None:
        """Toggle mute state."""
        self.set_mute(not self.mute_status())

    def movie_mode(self, input_source: str = "TV", volume: int = 50) -> None:
        """Helper to prepare receiver for movie night."""
        self.power_on()
        self.select_input(input_source)
        self.set_mute(False)
        self.set_volume(volume)
