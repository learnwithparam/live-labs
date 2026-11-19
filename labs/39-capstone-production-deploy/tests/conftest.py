"""Every test builds its own app from fakes and writes under a scratch folder, never the lab's own out/."""

import pytest

from brickhaven_api.breaker import CircuitBreaker
from brickhaven_api.config import BREAKER_COOLDOWN_S, BREAKER_FAILURES, FALLBACK_MODEL, PRIMARY_MODEL
from brickhaven_api.keys import ClientKeys
from brickhaven_api.ledger import Ledger
from brickhaven_api.limiter import RateLimiter
from brickhaven_api.loadtest import demo_keys
from brickhaven_api.router import Router
from brickhaven_api.service import App
from fakes import FakeClock, FakeProvider


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def make_app(tmp_path, clock):
    def build(provider=None, hardened=True) -> App:
        provider = provider or FakeProvider()
        breakers = {model: CircuitBreaker(BREAKER_FAILURES, BREAKER_COOLDOWN_S, clock) for model in (PRIMARY_MODEL, FALLBACK_MODEL)}
        router = Router(provider, PRIMARY_MODEL, FALLBACK_MODEL, breakers, clock)
        return App(provider, router, ClientKeys(), RateLimiter(clock), Ledger(tmp_path / "ledger.jsonl"), hardened=hardened,
                   changes_path=tmp_path / "changes.jsonl")
    return build


@pytest.fixture
def maple_header():
    return f"Bearer {demo_keys()['maple-portal']}"
