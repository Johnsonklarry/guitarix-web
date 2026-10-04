"""Amazon Alexa announcements and routines through Home Assistant.

Amazon has no supported local API.  The recommended path is Home Assistant,
either through the Alexa Media Player custom integration or a plain ``notify``
service.  This client talks to the Home Assistant REST API with a long-lived
access token and uses the Alexa Media Player ``notify.alexa_media`` service for
announcements and the ``script.turn_on`` service for routines (expose each
Alexa routine as a Home Assistant script named like the routine).

Amazon's only alternative is a custom Smart Home skill hosted in Amazon's
cloud with account linking; that is out of scope here.

Environment variables (never logged, never placed in exceptions):
    ALEXA_HA_URL     Home Assistant base URL, e.g. http://homeassistant:8123
    ALEXA_HA_TOKEN   Home Assistant long-lived access token

The JSON calls below are unverified against a live device and must be checked
before building:
    POST /api/services/notify/alexa_media
         {"message": ..., "target": <echo>, "data": {"type": "announce"}}
    POST /api/services/script/turn_on
         {"entity_id": "script.<routine>"}
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

__all__ = ["AlexaError", "Client", "from_env"]


class AlexaError(RuntimeError):
    """A Home Assistant Alexa call failed; the message never contains the token."""


def _script_entity_id(name):
    """Return a Home Assistant script entity id for an Alexa routine name."""
    if not isinstance(name, str) or not name.strip():
        raise AlexaError("routine name must be a non-empty string")
    if "." in name:
        return name
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    if not slug:
        raise AlexaError("routine name must contain a letter or digit")
    return f"script.{slug}"


class Client:
    """Home Assistant REST client for Alexa announcements and routines."""

    def __init__(self, base_url, token, *, timeout=10, notify_service="alexa_media"):
        if not base_url:
            raise AlexaError("base_url is required")
        if not token:
            raise AlexaError("token is required")
        self.base_url = str(base_url).rstrip("/")
        self.token = token
        self.timeout = timeout
        self.notify_service = notify_service

    def __repr__(self):
        return f"Client(base_url={self.base_url!r}, notify_service={self.notify_service!r})"

    @classmethod
    def from_env(cls, env=None):
        """Build a client from ALEXA_HA_URL / ALEXA_HA_TOKEN (or HA_URL / HA_TOKEN)."""
        env = os.environ if env is None else env
        base_url = env.get("ALEXA_HA_URL") or env.get("HA_URL")
        token = env.get("ALEXA_HA_TOKEN") or env.get("HA_TOKEN")
        if not base_url or not token:
            raise AlexaError("ALEXA_HA_URL and ALEXA_HA_TOKEN must be set")
        return cls(base_url, token)

    def announce(self, text, echo, *, announce=True):
        """Announce ``text`` on the named Echo (a device name or entity id)."""
        if not isinstance(text, str) or not text:
            raise AlexaError("announcement text must be a non-empty string")
        if not echo:
            raise AlexaError("an Echo name is required")
        data = {"message": text, "target": echo}
        if announce:
            data["data"] = {"type": "announce"}
        return self._call_service("notify", self.notify_service, data)

    def run_routine(self, name):
        """Run an Alexa routine exposed as a Home Assistant script."""
        return self._call_service("script", "turn_on", {"entity_id": _script_entity_id(name)})

    def _call_service(self, domain, service, data):
        url = f"{self.base_url}/api/services/{domain}/{service}"
        body = json.dumps(data).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            raise AlexaError(
                f"Home Assistant {domain}.{service} failed with HTTP {exc.code}"
            ) from None
        except (urllib.error.URLError, TimeoutError):
            raise AlexaError(f"Home Assistant at {self.base_url} is unreachable") from None
        if not payload:
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except ValueError:
            return None


def from_env(env=None):
    """Convenience wrapper for :meth:`Client.from_env`."""
    return Client.from_env(env)
