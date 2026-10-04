"""Authentik forward-auth identity extraction and validation."""

from __future__ import annotations

from typing import Mapping, Optional


class AuthentikAuthError(ValueError):
    """Raised when forward-auth headers are missing, malformed, or untrusted."""


def extract_authentik_identity(
    headers: Mapping[str, str],
    *,
    trusted_proxy: bool = False,
    header_name: str = "X-authentik-username",
) -> str:
    """Extract and normalize a stable user identifier from forward-auth headers.

    Requires an explicit trusted-proxy context (trusted_proxy=True).
    Rejects missing, untrusted, or malformed/empty header values.
    """
    if not trusted_proxy:
        raise AuthentikAuthError("Untrusted proxy context: forward-auth headers rejected")

    # Case-insensitive lookup for headers
    target_key = header_name.lower()
    value: Optional[str] = None
    for k, v in headers.items():
        if k.lower() == target_key:
            value = v
            break

    if value is None:
        raise AuthentikAuthError(f"Missing required header: {header_name}")

    normalized = value.strip()
    if not normalized:
        raise AuthentikAuthError(f"Malformed or empty identity in header: {header_name}")

    return normalized
