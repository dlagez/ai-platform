from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class CircuitBreakerSettings:
    sliding_window_seconds: int = 60
    min_requests: int = 20
    failure_rate_threshold: float = 0.5
    open_seconds: int = 30
    half_open_probe_requests: int = 5
    half_open_success_threshold: float = 0.8


@dataclass
class _CircuitRecord:
    state: CircuitState = CircuitState.CLOSED
    events: deque[tuple[float, bool]] = field(default_factory=deque)
    open_until_ts: float = 0.0
    half_open_probe_count: int = 0
    half_open_success_count: int = 0


class CircuitBreaker:
    def __init__(self, settings: CircuitBreakerSettings | None = None) -> None:
        self.settings = settings or CircuitBreakerSettings()
        self._records: dict[str, _CircuitRecord] = {}

    def _record(self, key: str) -> _CircuitRecord:
        if key not in self._records:
            self._records[key] = _CircuitRecord()
        return self._records[key]

    def _now(self) -> float:
        return time.monotonic()

    def _maybe_transition_open_to_half_open(self, rec: _CircuitRecord, now: float) -> None:
        if rec.state == CircuitState.OPEN and now >= rec.open_until_ts:
            rec.state = CircuitState.HALF_OPEN
            rec.half_open_probe_count = 0
            rec.half_open_success_count = 0

    def state(self, key: str, now: float | None = None) -> CircuitState:
        now = self._now() if now is None else now
        rec = self._record(key)
        self._maybe_transition_open_to_half_open(rec, now)
        return rec.state

    def allow_request(self, key: str, now: float | None = None) -> bool:
        now = self._now() if now is None else now
        rec = self._record(key)
        self._maybe_transition_open_to_half_open(rec, now)

        if rec.state == CircuitState.OPEN:
            return False
        if rec.state == CircuitState.HALF_OPEN:
            return rec.half_open_probe_count < self.settings.half_open_probe_requests
        return True

    def record_result(self, key: str, success: bool, now: float | None = None) -> None:
        now = self._now() if now is None else now
        rec = self._record(key)
        self._maybe_transition_open_to_half_open(rec, now)

        if rec.state == CircuitState.HALF_OPEN:
            rec.half_open_probe_count += 1
            if success:
                rec.half_open_success_count += 1

            if rec.half_open_probe_count >= self.settings.half_open_probe_requests:
                success_rate = rec.half_open_success_count / max(rec.half_open_probe_count, 1)
                if success_rate >= self.settings.half_open_success_threshold:
                    rec.state = CircuitState.CLOSED
                    rec.events.clear()
                else:
                    rec.state = CircuitState.OPEN
                    rec.open_until_ts = now + self.settings.open_seconds
            return

        if rec.state == CircuitState.CLOSED:
            rec.events.append((now, success))
            window_start = now - self.settings.sliding_window_seconds
            while rec.events and rec.events[0][0] < window_start:
                rec.events.popleft()

            if len(rec.events) < self.settings.min_requests:
                return

            failures = sum(1 for _, ok in rec.events if not ok)
            failure_rate = failures / len(rec.events)
            if failure_rate >= self.settings.failure_rate_threshold:
                rec.state = CircuitState.OPEN
                rec.open_until_ts = now + self.settings.open_seconds

