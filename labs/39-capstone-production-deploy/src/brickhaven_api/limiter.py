"""A token bucket per client: a burst is allowed, a sustained flood is not, and the refusal says when to return.

A fixed counter per minute lets a client spend its whole minute in the first second and then lets it
back in at :00 with a fresh minute. A bucket refills a little all the time, so the pace is steady.
"""

import math
import threading
import time
from typing import Callable


class TokenBucket:
    def __init__(self, per_minute: float, burst: int, clock: Callable[[], float] = time.monotonic):
        self.refill_per_second = per_minute / 60
        self.capacity = float(burst)
        self.tokens = float(burst)
        self.clock = clock
        self.updated = clock()

    def take(self) -> tuple[bool, float]:
        """Spend one token. Returns whether it was allowed and, if not, seconds until one is back."""
        now = self.clock()
        # Refill for the time that has passed, never past the bucket's size.
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill_per_second)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True, 0.0
        return False, (1 - self.tokens) / self.refill_per_second


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.buckets: dict[str, TokenBucket] = {}
        self.lock = threading.Lock()

    def configure(self, name: str, per_minute: float, burst: int) -> None:
        with self.lock:
            self.buckets[name] = TokenBucket(per_minute, burst, self.clock)

    def take(self, name: str) -> tuple[bool, int]:
        """Allowed or not, and the whole seconds a refused client should wait (the Retry-After value)."""
        with self.lock:
            allowed, wait_seconds = self.buckets[name].take()
        return allowed, 0 if allowed else max(1, math.ceil(wait_seconds))
