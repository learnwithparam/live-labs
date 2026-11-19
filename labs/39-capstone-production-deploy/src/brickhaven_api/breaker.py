"""A circuit breaker: stop calling a model that keeps failing, and try it again after a cooldown.

Without it every request waits the full timeout on a dead model before it reaches the fallback, so a
provider outage turns into a latency outage. With it the first few requests pay, and the rest skip the
dead model at once.
"""

import threading
import time
from typing import Callable

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half-open"


class CircuitBreaker:
    def __init__(self, failure_threshold: int, cooldown_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self.failures = 0
        self.opened_at: float | None = None
        self.probing = False
        self.lock = threading.Lock()

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return CLOSED
        return HALF_OPEN if self.clock() - self.opened_at >= self.cooldown_seconds else OPEN

    def allow(self) -> bool:
        """Closed: yes. Open: no. Half-open: exactly one probe call, so a still-dead model is not flooded."""
        with self.lock:
            state = self.state
            if state == CLOSED:
                return True
            if state == HALF_OPEN and not self.probing:
                self.probing = True
                return True
            return False

    def record_success(self) -> None:
        with self.lock:
            self.failures, self.opened_at, self.probing = 0, None, False

    def record_failure(self) -> None:
        with self.lock:
            self.failures += 1
            # A failed probe reopens at once; otherwise it takes a run of failures to open.
            if self.probing or self.failures >= self.failure_threshold:
                self.opened_at, self.probing = self.clock(), False
