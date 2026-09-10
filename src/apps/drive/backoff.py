"""Backoff und Ratenbegrenzung fuer Drive-Aufrufe (Fachentwurf F 2.5, ANNAHME A-22, A-23).

retry_call wiederholt transiente Fehler (403 Ratenlimit, 429, 5xx, Netzwerk) mit exponentiellem Backoff und vollem
Zufallsanteil, beachtet Retry-After und gibt nach max_attempts auf. Uhr, Schlaffunktion und Zufall sind injizierbar,
damit Tests keine echte Zeit verbrauchen.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from apps.drive.adapter import DriveError, RateLimited, TransientError


@dataclass
class BackoffConfig:
    base_s: float = 1.0
    factor: float = 2.0
    max_delay_s: float = 64.0
    max_attempts: int = 8

    @classmethod
    def from_setting(cls, value: dict | None) -> BackoffConfig:
        value = value or {}
        # Seed drive.backoff: base_s, factor, max_s, attempts (docs/architektur.md 5.5)
        return cls(
            base_s=float(value.get("base_s", 1.0)),
            factor=float(value.get("factor", 2.0)),
            max_delay_s=float(value.get("max_s", value.get("max_delay_s", 64.0))),
            max_attempts=int(value.get("attempts", value.get("max_attempts", 8))),
        )


@dataclass
class CallMetrics:
    attempts: int = 0
    retries: int = 0
    rate_limited: int = 0
    server_errors: int = 0
    waited_s: float = 0.0
    failures: int = 0


def retry_call(
    fn: Callable,
    *,
    config: BackoffConfig | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    metrics: CallMetrics | None = None,
    on_auth_error: Callable[[], None] | None = None,
):
    """Fuehrt fn aus; RateLimited und TransientError werden wiederholt, AuthError einmal nach on_auth_error."""
    cfg = config or BackoffConfig()
    rng = rng or random.Random()
    metrics = metrics if metrics is not None else CallMetrics()
    auth_retried = False
    attempt = 0
    while True:
        attempt += 1
        metrics.attempts += 1
        try:
            return fn()
        except (RateLimited, TransientError) as exc:
            if isinstance(exc, RateLimited):
                metrics.rate_limited += 1
            else:
                metrics.server_errors += 1
            if attempt >= cfg.max_attempts:
                metrics.failures += 1
                raise
            cap = min(cfg.max_delay_s, cfg.base_s * (cfg.factor ** (attempt - 1)))
            delay = exc.retry_after if exc.retry_after is not None else rng.uniform(0, cap)
            metrics.retries += 1
            metrics.waited_s += delay
            sleep(delay)
        except DriveError as exc:
            from apps.drive.adapter import AuthError

            if isinstance(exc, AuthError) and on_auth_error is not None and not auth_retried:
                auth_retried = True
                on_auth_error()
                continue
            metrics.failures += 1
            raise


class RateLimiter:
    """Token-Bucket: hoechstens rate Aufrufe je Sekunde (drive.max_requests_per_second)."""

    def __init__(
        self,
        rate_per_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        burst: int | None = None,
    ):
        self.rate = max(rate_per_s, 0.001)
        self.capacity = burst or max(1, int(self.rate))
        self.tokens = float(self.capacity)
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        with self._lock:
            now = self._clock()
            self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
            self._last = now
            if self.tokens >= 1:
                self.tokens -= 1
                return 0.0
            wait = (1 - self.tokens) / self.rate
            self._sleep(wait)
            self.tokens = 0.0
            self._last = self._clock()
            return wait
