"""Immutable redeploy approvals with a side-effect-free creation path.

Creating a :class:`RedeployRequest` only validates and binds the caller's
inputs. It never contacts Portainer, TrueNAS, or any deployment executor, so
inspecting a freshly created request cannot trigger a deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

__all__ = ["RedeployRequest", "create_redeploy_request"]


@dataclass(frozen=True)
class RedeployRequest:
    """An immutable approval to redeploy one stack at an exact revision."""

    stack: str
    endpoint: str
    revision: str
    requester: str
    expires_at: datetime


def _require_text(name, value):
    """Return ``value`` if it is a non-empty string, otherwise raise."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _as_utc(value):
    """Treat naive datetimes as UTC and normalise aware ones to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def create_redeploy_request(stack, endpoint, revision, requester, expires_at, *, now=None):
    """Validate and freeze a redeploy approval without performing any action.

    ``stack``, ``endpoint``, ``revision`` and ``requester`` must be non-empty
    strings, and ``expires_at`` must be a :class:`datetime` in the future.
    The optional ``now`` pins the clock for tests and defaults to UTC now.
    """
    stack = _require_text("stack", stack)
    endpoint = _require_text("endpoint", endpoint)
    revision = _require_text("revision", revision)
    requester = _require_text("requester", requester)
    if not isinstance(expires_at, datetime):
        raise TypeError("expires_at must be a datetime")
    reference = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    if not _as_utc(expires_at) > reference:
        raise ValueError("expires_at must be in the future")
    return RedeployRequest(
        stack=stack,
        endpoint=endpoint,
        revision=revision,
        requester=requester,
        expires_at=expires_at,
    )
