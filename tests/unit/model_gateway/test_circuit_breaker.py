from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_ROOT = ROOT / "apps" / "platform-service" / "modules" / "model-gateway"
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from model_gateway.circuit_breaker import CircuitBreaker, CircuitBreakerSettings, CircuitState


class CircuitBreakerTests(unittest.TestCase):
    def test_open_and_half_open_transition(self) -> None:
        settings = CircuitBreakerSettings(
            sliding_window_seconds=60,
            min_requests=4,
            failure_rate_threshold=0.5,
            open_seconds=1,
            half_open_probe_requests=2,
            half_open_success_threshold=0.5,
        )
        breaker = CircuitBreaker(settings=settings)
        key = "openai:gpt-4o-mini"

        t0 = 1000.0
        for i in range(4):
            breaker.record_result(key, success=False, now=t0 + i)
        self.assertEqual(breaker.state(key, now=t0 + 3.5), CircuitState.OPEN)
        self.assertFalse(breaker.allow_request(key, now=t0 + 3.6))

        # OPEN timeout elapsed -> HALF_OPEN.
        self.assertTrue(breaker.allow_request(key, now=t0 + 5.2))
        self.assertEqual(breaker.state(key, now=t0 + 5.2), CircuitState.HALF_OPEN)

    def test_half_open_back_to_closed(self) -> None:
        settings = CircuitBreakerSettings(
            sliding_window_seconds=60,
            min_requests=2,
            failure_rate_threshold=0.5,
            open_seconds=1,
            half_open_probe_requests=2,
            half_open_success_threshold=0.5,
        )
        breaker = CircuitBreaker(settings=settings)
        key = "mock:model"
        t0 = 2000.0

        breaker.record_result(key, success=False, now=t0)
        breaker.record_result(key, success=False, now=t0 + 0.1)
        self.assertEqual(breaker.state(key, now=t0 + 0.2), CircuitState.OPEN)

        # move to HALF_OPEN
        self.assertTrue(breaker.allow_request(key, now=t0 + 1.3))
        breaker.record_result(key, success=True, now=t0 + 1.3)
        breaker.record_result(key, success=True, now=t0 + 1.4)
        self.assertEqual(breaker.state(key, now=t0 + 1.5), CircuitState.CLOSED)


if __name__ == "__main__":
    unittest.main()
