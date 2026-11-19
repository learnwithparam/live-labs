"""The key check, the token bucket and the circuit breaker, each driven by a clock the test controls."""

import pytest

from brickhaven_api.breaker import CLOSED, HALF_OPEN, OPEN, CircuitBreaker
from brickhaven_api.config import DATA
from brickhaven_api.keys import ClientKeys
from brickhaven_api.limiter import RateLimiter, TokenBucket
from brickhaven_api.loadtest import demo_keys


def test_each_demo_key_opens_its_own_client_and_nothing_else():
    keys = ClientKeys()
    for name, key in demo_keys().items():
        assert keys.authenticate(f"Bearer {key}").name == name
    assert keys.authenticate("Bearer not-a-key") is None
    assert keys.authenticate("") is None and keys.authenticate("Bearer ") is None
    assert keys.authenticate(f"Basic {demo_keys()['maple-portal']}") is None


def test_the_clients_file_holds_hashes_and_never_a_key():
    text = (DATA / "clients.toml").read_text()
    assert all(key not in text for key in demo_keys().values())


def test_a_bucket_allows_its_burst_then_refuses_with_a_wait(clock):
    bucket = TokenBucket(per_minute=60, burst=3, clock=clock)
    assert [bucket.take()[0] for _ in range(3)] == [True, True, True]
    allowed, wait_seconds = bucket.take()
    assert not allowed and wait_seconds == pytest.approx(1.0)
    clock.advance(1)
    assert bucket.take()[0] and not bucket.take()[0]


def test_a_bucket_never_holds_more_than_its_burst(clock):
    bucket = TokenBucket(per_minute=60, burst=2, clock=clock)
    clock.advance(3600)
    assert [bucket.take()[0] for _ in range(3)] == [True, True, False]


def test_retry_after_is_whole_seconds_and_at_least_one(clock):
    limiter = RateLimiter(clock)
    limiter.configure("slow", per_minute=6, burst=1)
    assert limiter.take("slow") == (True, 0)
    assert limiter.take("slow") == (False, 10)
    limiter.configure("fast", per_minute=6000, burst=1)
    limiter.take("fast")
    assert limiter.take("fast") == (False, 1)


def test_one_clients_flood_does_not_touch_another(clock):
    limiter = RateLimiter(clock)
    limiter.configure("flood", per_minute=6, burst=1)
    limiter.configure("honest", per_minute=6, burst=1)
    limiter.take("flood")
    assert not limiter.take("flood")[0] and limiter.take("honest")[0]


def test_the_breaker_opens_after_a_run_of_failures_and_a_success_resets_the_run(clock):
    breaker = CircuitBreaker(3, 20, clock)
    breaker.record_failure(); breaker.record_failure(); breaker.record_success()
    breaker.record_failure(); breaker.record_failure()
    assert breaker.state == CLOSED and breaker.allow()
    breaker.record_failure()
    assert breaker.state == OPEN and not breaker.allow()


def test_after_the_cooldown_one_probe_is_let_through_and_its_result_decides(clock):
    breaker = CircuitBreaker(1, 20, clock)
    breaker.record_failure()
    clock.advance(19)
    assert not breaker.allow()
    clock.advance(1)
    assert breaker.state == HALF_OPEN and breaker.allow() and not breaker.allow()
    breaker.record_failure()
    assert breaker.state == OPEN
    clock.advance(20)
    assert breaker.allow()
    breaker.record_success()
    assert breaker.state == CLOSED and breaker.allow()
