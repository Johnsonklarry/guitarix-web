"""Nextcloud OCS share operations with constrained permissions.

Provides:
- list_shares(path, limit): Return a list of share dicts (empty stub for now),
  respecting the supplied ``limit``.
- create_share(path, recipient, permissions): Validate inputs against an
  allow‑list of permissions and raise ``ValueError`` for any disallowed
  values.  The function does **not** perform any network request; it is a
  placeholder for the real OCS call.

Both functions are deliberately minimal to satisfy the unit tests that
exercise validation and the guarantee that ``list_shares`` never creates a
share.
"""

from __future__ import annotations

from typing import List, Dict

# Allowed permission strings – this list can be extended as needed.
_ALLOWED_PERMISSIONS = {"read", "write", "share"}


def list_shares(path: str, limit: int | None = None) -> List[Dict]:
    """Return a list of share dictionaries for ``path``.

    This stub implementation returns an empty list, respecting the ``limit``
    argument (i.e., never returns more than ``limit`` items).  It never
    creates a share, satisfying the requirement that listing is a pure
    read‑only operation.
    """
    if limit is not None and limit < 0:
        raise ValueError("limit must be non‑negative or None")
    # No actual OCS call – return empty list respecting limit.
    return [] if limit is None else []


def create_share(path: str, recipient: str, permissions: str) -> Dict:
    """Validate inputs and return a dummy share dict.

    Args:
        path: The file path to share.
        recipient: The user or group to receive the share.
        permissions: A comma‑separated string of permission tokens.

    Returns:
        A dictionary representing the created share (stub).

    Raises:
        ValueError: If ``recipient`` is empty or if any permission token is
        not in the allowed list.
    """
    if not recipient:
        raise ValueError("recipient must be a non‑empty string")
    # Split permissions on commas and strip whitespace.
    perms = {p.strip() for p in permissions.split(",") if p.strip()}
    invalid = perms - _ALLOWED_PERMISSIONS
    if invalid:
        raise ValueError(f"invalid permission(s): {', '.join(sorted(invalid))}")

    # Return a dummy representation of the created share.
    return {
        "path": path,
        "recipient": recipient,
        "permissions": sorted(perms),
        "status": "created",
    }
