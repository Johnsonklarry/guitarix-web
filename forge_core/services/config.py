"""Service configuration and credential loading.

Validated connection settings for the services forge talks to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import stat
from types import MappingProxyType
from typing import Mapping
from urllib.parse import parse_qsl, urlsplit

__all__ = [
    "Credential",
    "SERVICE_IDS",
    "ServiceConfig",
    "load_credential",
    "validate_base_url",
]

#: The nine services forge knows how to talk to; any other id is rejected.
SERVICE_IDS: tuple[str, ...] = (
    "delegation",
    "guitarix",
    "family",
    "ffmpeg",
    "mpv",
    "jack",
    "obs",
    "mediamtx",
    "openai",
    "audiobookshelf",
    "nextcloud",
    "joplin",
    "spotify",
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Query keys that signal a credential smuggled into a base URL.
_SECRET_QUERY_KEYS = frozenset({
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "credential",
    "credentials",
    "key",
    "password",
    "passwd",
    "secret",
    "sig",
    "signature",
    "token",
})


class Credential:
    """A container for sensitive credential values that avoids accidental exposure."""

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<Credential: [REDACTED]>"

    def __str__(self) -> str:
        return "[REDACTED]"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Credential):
            return self._value == other._value
        if isinstance(other, str):
            return self._value == other
        return NotImplemented


def load_credential(env_var: str | None = None, file_path: str | None = None) -> Credential:
    """Load a credential from an environment variable or a mode-600 regular file.

    Rejects permissive file permissions (anything accessible by group/others or executable).
    Credentials are never exposed in error messages or representations.
    """
    if env_var:
        val = os.environ.get(env_var)
        if val is not None:
            return Credential(val)

    if file_path:
        st = os.stat(file_path)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("Credential file must be a regular file")

        # Must not be executable by user, and must not have any permissions for group or others
        # Mode-600 (stat.S_IRUSR | stat.S_IWUSR) or read-only mode-400 (stat.S_IRUSR)
        permissive_mask = stat.S_IRWXG | stat.S_IRWXO | stat.S_IXUSR
        if st.st_mode & permissive_mask:
            raise PermissionError("Credential file has permissive permissions; mode 0600 or stricter required")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().rstrip("\r\n")
        return Credential(content)

    raise ValueError("Either env_var or file_path must be specified and contain a credential")


def validate_base_url(url: str) -> str:
    """Return *url* when it is a safe service base URL.

    A safe URL uses ``http`` or ``https``, names a host, and carries neither
    userinfo (``user:password@host``) nor credential-looking query
    parameters.  Anything else raises :class:`ValueError`.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("base_url must be a non-empty string")
    if url != url.strip():
        raise ValueError(f"base_url must not have surrounding whitespace: {url!r}")
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError(f"base_url must use http or https: {url!r}")
    if not parts.hostname:
        raise ValueError(f"base_url must include a host: {url!r}")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"base_url must not contain userinfo: {url!r}")
    if parts.fragment:
        raise ValueError(f"base_url must not contain a fragment: {url!r}")
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _SECRET_QUERY_KEYS or value.lower().startswith(("bearer ", "token ")):
            raise ValueError(
                f"base_url must not carry credentials in query parameters: {url!r}"
            )
    return url


@dataclass(frozen=True)
class ServiceConfig:
    """Base URLs and credentials for the nine forge services, keyed by service id.

    Only ids in :data:`SERVICE_IDS` are accepted. Every URL is validated
    by :func:`validate_base_url` and every credential is loaded via
    :func:`load_credential` when the instance is created.
    """

    #: The nine predefined service ids (mirrors :data:`SERVICE_IDS`).
    SERVICE_IDS = SERVICE_IDS

    base_urls: Mapping[str, str] = field(default_factory=dict)
    credential_sources: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    credentials: Mapping[str, Credential] = field(init=False, default_factory=dict)

    def __post_init__(self):
        unknown = sorted(set(self.base_urls) - set(SERVICE_IDS))
        if unknown:
            raise ValueError(f"unknown service ids: {unknown}")
        validated = {sid: validate_base_url(url) for sid, url in self.base_urls.items()}
        object.__setattr__(self, "base_urls", MappingProxyType(validated))

        unknown_creds = sorted(set(self.credential_sources) - set(SERVICE_IDS))
        if unknown_creds:
            raise ValueError(f"unknown service ids in credential_sources: {unknown_creds}")
        credentials = {}
        for sid, source in self.credential_sources.items():
            if "env_var" in source and "file_path" in source:
                raise ValueError(f"credential source for {sid!r} must specify either env_var or file_path, not both")
            if "env_var" in source:
                credentials[sid] = load_credential(env_var=source["env_var"])
            elif "file_path" in source:
                credentials[sid] = load_credential(file_path=source["file_path"])
            else:
                raise ValueError(f"credential source for {sid!r} must specify env_var or file_path")
        object.__setattr__(self, "credentials", MappingProxyType(credentials))

    def url_for(self, service_id: str) -> str:
        """Return the configured base URL for *service_id*."""
        if service_id not in SERVICE_IDS:
            raise KeyError(service_id)
        try:
            return self.base_urls[service_id]
        except KeyError:
            raise KeyError(f"no base URL configured for {service_id!r}") from None

    def credential_for(self, service_id: str) -> Credential:
        """Return the loaded credential for *service_id*."""
        if service_id not in SERVICE_IDS:
            raise KeyError(service_id)
        try:
            return self.credentials[service_id]
        except KeyError:
            raise KeyError(f"no credential configured for {service_id!r}") from None
