"""HTTP service client with injectable transport, timeouts, and auth headers."""

from __future__ import annotations

import threading
import time
import urllib.request
from typing import Any, Callable, Mapping


class RateLimitExceeded(Exception):
    """Raised when rate limit wait budget is exhausted."""


class RateLimiter:
    """Thread-safe per-service rate limiter with an injectable clock and wait budget."""

    def __init__(
        self,
        rate: float,
        per: float = 1.0,
        clock: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        self.rate = float(rate)
        self.per = float(per)
        self.clock = clock or time.monotonic
        self.sleep = sleep_fn or time.sleep
        self.interval = self.per / self.rate if self.rate > 0 else 0.0
        self._lock = threading.Lock()
        self._last_allowed = 0.0

    def acquire(self, max_wait: float | None = None) -> None:
        with self._lock:
            now = self.clock()
            earliest_time = self._last_allowed + self.interval
            wait_time = max(0.0, earliest_time - now)
            if max_wait is not None and wait_time > max_wait:
                raise RateLimitExceeded(
                    f"Rate limit wait time {wait_time:.3f}s exceeds budget of {max_wait:.3f}s"
                )
            if wait_time > 0:
                self.sleep(wait_time)
                now = self.clock()
            self._last_allowed = now


class ServiceClient:
    """Service client performing HTTP requests via an injectable stdlib transport."""

    def __init__(self, config: Mapping[str, Any] | None = None, transport: Any = None):
        self.config = dict(config or {})
        self.transport = transport or urllib.request.urlopen
        self.connect_timeout = float(self.config.get("connect_timeout", 5.0))
        self.read_timeout = float(self.config.get("read_timeout", 10.0))
        self.timeout = self.connect_timeout + self.read_timeout
        self.auth_headers = dict(self.config.get("auth_headers", {}))

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        data: bytes | None = None,
    ) -> Any:
        combined_headers = dict(self.auth_headers)
        if headers:
            combined_headers.update(headers)
        req = urllib.request.Request(url, data=data, headers=combined_headers, method=method)
        return self.transport(req, timeout=self.timeout)
