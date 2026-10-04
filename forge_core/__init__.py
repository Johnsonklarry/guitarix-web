"""Shared Forge dashboard utilities.

This module also provides the bounded health-probe runner used to check
optional services. Probes execute their transport under a timeout and
normalize the outcome to one of :data:`STATUSES`; reported details are
truncated and secret-redacted so they are safe to surface to callers.
"""

from __future__ import annotations

import socket
import urllib.error

from forge_core.plex import DEFAULT_TIMEOUT, PLEX_DEFAULT_URL, PlexClient, ServiceClient

VERSION = "1.0.0"

HEALTHY = "healthy"
UNAVAILABLE = "unavailable"
AUTH_FAILED = "auth-failed"
UNCONFIGURED = "unconfigured"

STATUSES = (HEALTHY, UNAVAILABLE, AUTH_FAILED, UNCONFIGURED)

DEFAULT_TIMEOUT = 2.0
DEFAULT_DETAIL_LIMIT = 160

_AUTH_HTTP_CODES = (401, 403)
_TIMEOUT_ERRORS = (TimeoutError, socket.timeout)

__all__ = [
    "AUTH_FAILED",
    "DEFAULT_DETAIL_LIMIT",
    "DEFAULT_TIMEOUT",
    "HEALTHY",
    "PLEX_DEFAULT_URL",
    "PlexClient",
    "ProbeResult",
    "Reachability",
    "Runner",
    "STATUSES",
    "ServiceClient",
    "UNAVAILABLE",
    "UNCONFIGURED",
    "VERSION",
]


class ProbeResult:
    """A normalized, secret-free outcome from a single health probe.

    ``status`` is one of :data:`STATUSES`. ``reachable`` records that the
    transport answered, while ``functional`` records that a caller-supplied
    assessment confirmed the service is actually working; reachability alone
    must never be reported as ``functional``.
    """

    __slots__ = ("status", "detail", "reachable", "functional")

    def __init__(self, status, detail="", *, reachable=False, functional=False):
        if status not in STATUSES:
            raise ValueError(f"unknown probe status: {status!r}")
        if functional and not reachable:
            raise ValueError("a functional result must also be reachable")
        self.status = status
        self.detail = detail
        self.reachable = bool(reachable)
        self.functional = bool(functional)

    def as_dict(self):
        return {
            "status": self.status,
            "detail": self.detail,
            "reachable": self.reachable,
            "functional": self.functional,
        }

    def __eq__(self, other):
        if not isinstance(other, ProbeResult):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def __repr__(self):
        return (
            f"ProbeResult({self.status!r}, {self.detail!r}, "
            f"reachable={self.reachable!r}, functional={self.functional!r})"
        )


class Reachability:
    """A bounded reachability observation that makes no functionality claim."""

    __slots__ = ("reachable", "detail")

    def __init__(self, reachable, detail=""):
        self.reachable = bool(reachable)
        self.detail = detail

    def __eq__(self, other):
        if not isinstance(other, Reachability):
            return NotImplemented
        return (self.reachable, self.detail) == (other.reachable, other.detail)

    def __repr__(self):
        return f"Reachability({self.reachable!r}, {self.detail!r})"


class Runner:
    """Execute bounded health probes and normalize their outcomes.

    ``transport`` is a callable ``transport(timeout)``. Raising an exception
    means the endpoint could not be reached; returning a value means it
    answered. A successful transport is only proof of reachability, so
    :meth:`run` requires an ``assess`` callable before it will report
    ``healthy``; use :meth:`reachability` for a bounded reachability-only
    check.
    """

    def __init__(self, timeout=DEFAULT_TIMEOUT, detail_limit=DEFAULT_DETAIL_LIMIT, secrets=()):
        if timeout is None or timeout <= 0:
            raise ValueError("timeout must be positive")
        if detail_limit < 0:
            raise ValueError("detail_limit cannot be negative")
        self.timeout = timeout
        self.detail_limit = detail_limit
        self.secrets = tuple(secret for secret in secrets if secret)

    def _bounded(self, message):
        text = " ".join(str(message).split())
        for secret in self.secrets:
            text = text.replace(secret, "[redacted]")
        if len(text) > self.detail_limit:
            text = text[: self.detail_limit] + "..."
        return text

    def run(self, transport, *, configured=True, assess=None):
        """Run ``transport`` under a timeout and normalize the outcome.

        ``assess`` receives the transport's return value and decides whether
        the service is genuinely functional. It is required so that a bare
        reachability check cannot be reported as :data:`HEALTHY`.
        """
        if not configured:
            return ProbeResult(UNCONFIGURED, "not configured")
        if assess is None:
            raise ValueError("assess is required; use reachability() for reachability-only checks")

        try:
            response = transport(self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in _AUTH_HTTP_CODES:
                return ProbeResult(AUTH_FAILED, self._bounded(f"http {exc.code}"), reachable=True)
            return ProbeResult(UNAVAILABLE, self._bounded(f"http {exc.code}"))
        except _TIMEOUT_ERRORS:
            return ProbeResult(UNAVAILABLE, "timed out")
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, _TIMEOUT_ERRORS):
                return ProbeResult(UNAVAILABLE, "timed out")
            return ProbeResult(UNAVAILABLE, self._bounded(reason))
        except OSError as exc:
            return ProbeResult(UNAVAILABLE, self._bounded(exc))
        except Exception as exc:  # transport-specific failures stay bounded
            return ProbeResult(UNAVAILABLE, self._bounded(exc))

        try:
            functional = bool(assess(response))
        except Exception as exc:
            return ProbeResult(UNAVAILABLE, self._bounded(exc), reachable=True)
        if functional:
            return ProbeResult(HEALTHY, "ok", reachable=True, functional=True)
        return ProbeResult(UNAVAILABLE, "reachable but not functional", reachable=True)

    def reachability(self, transport):
        """Return a bounded reachability observation without claiming health."""
        try:
            transport(self.timeout)
        except _TIMEOUT_ERRORS:
            return Reachability(False, "timed out")
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, _TIMEOUT_ERRORS):
                return Reachability(False, "timed out")
            return Reachability(False, self._bounded(reason))
        except OSError as exc:
            return Reachability(False, self._bounded(exc))
        except Exception as exc:
            return Reachability(False, self._bounded(exc))
        return Reachability(True, "reachable")
