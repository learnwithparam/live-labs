"""The service end to end with a fake provider: key, limit, fallback, breaker, ledger and the raise-limit decision."""

import pytest

from brickhaven_api.config import FALLBACK_MODEL, PRIMARY_MODEL
from fakes import FakeProvider

MESSAGE = {"message": "Unit 4B, the kitchen tap drips."}


def test_a_good_call_returns_a_work_order_and_books_one_ledger_row(make_app, maple_header):
    app = make_app()
    reply = app.handle_work_order(maple_header, MESSAGE)
    assert reply.status == 200 and reply.payload["work_order"]["trade"] == "plumbing" and reply.payload["served_by"] == "primary"
    assert len(app.ledger.rows) == 1 and app.ledger.rows[0]["client"] == "maple-portal"


def test_the_caller_never_sees_a_model_name_or_a_cost(make_app, maple_header):
    reply = make_app().handle_work_order(maple_header, MESSAGE)
    text = str(reply.payload) + str(reply.headers)
    assert PRIMARY_MODEL not in text and FALLBACK_MODEL not in text and "cost" not in text


def test_no_key_and_a_wrong_key_are_refused_before_any_model_call(make_app):
    provider = FakeProvider()
    app = make_app(provider)
    assert app.handle_work_order("", MESSAGE).status == 401
    assert app.handle_work_order("Bearer nope", MESSAGE).status == 401
    assert provider.calls == []


def test_a_flood_gets_429_with_retry_after_and_the_refused_calls_cost_nothing(make_app):
    provider = FakeProvider()
    app = make_app(provider)
    from brickhaven_api.loadtest import demo_keys
    header = f"Bearer {demo_keys()['runaway-sync']}"
    statuses = [app.handle_work_order(header, MESSAGE) for _ in range(6)]
    assert [reply.status for reply in statuses] == [200, 200, 200, 429, 429, 429]
    assert int(statuses[3].headers["Retry-After"]) >= 1
    assert len(provider.calls) == 3 and app.usage["runaway-sync"]["limited"] == 3


def test_a_bad_message_is_a_400_and_costs_nothing(make_app, maple_header):
    provider = FakeProvider()
    app = make_app(provider)
    assert app.handle_work_order(maple_header, {}).status == 400
    assert app.handle_work_order(maple_header, {"message": "x" * 5000}).status == 400
    assert provider.calls == []


def test_when_the_primary_fails_the_fallback_answers(make_app, maple_header):
    app = make_app(FakeProvider(failing={PRIMARY_MODEL}))
    reply = app.handle_work_order(maple_header, MESSAGE)
    assert reply.status == 200 and reply.payload["served_by"] == "fallback"
    assert app.ledger.rows[0]["model"] == FALLBACK_MODEL


def test_the_breaker_stops_calls_to_a_dead_primary_then_probes_it_after_the_cooldown(make_app, maple_header, clock):
    provider = FakeProvider(failing={PRIMARY_MODEL})
    app = make_app(provider)
    for _ in range(3):
        app.handle_work_order(maple_header, MESSAGE)
    assert app.router.breaker_states()["primary"] == "open"
    provider.calls.clear()
    app.handle_work_order(maple_header, MESSAGE)
    assert provider.calls == [FALLBACK_MODEL]
    provider.failing.clear()
    clock.advance(21)
    reply = app.handle_work_order(maple_header, MESSAGE)
    assert provider.calls[-1] == PRIMARY_MODEL and reply.payload["served_by"] == "primary"
    assert app.router.breaker_states()["primary"] == "closed"


def test_both_models_down_is_a_503_with_retry_after(make_app, maple_header):
    app = make_app(FakeProvider(failing={PRIMARY_MODEL, FALLBACK_MODEL}))
    reply = app.handle_work_order(maple_header, MESSAGE)
    assert reply.status == 503 and reply.headers["Retry-After"] and app.usage["maple-portal"]["failed"] == 1


def test_an_answer_that_is_not_a_work_order_is_a_502_but_the_spend_is_still_booked(make_app, maple_header):
    app = make_app(FakeProvider(answer="I cannot help with that"))
    reply = app.handle_work_order(maple_header, MESSAGE)
    assert reply.status == 502 and len(app.ledger.rows) == 1


def test_a_fallback_call_counts_the_time_the_failed_primary_cost(make_app, maple_header, clock):
    class SlowFailure(FakeProvider):
        def complete(self, model, system, user):
            clock.advance(3.5)
            return super().complete(model, system, user)
    app = make_app(SlowFailure(failing={PRIMARY_MODEL}, first_token_ms=100))
    # The failed primary took 3500 ms before the fallback started, and the fallback's own first token took 100 ms.
    assert app.handle_work_order(maple_header, MESSAGE).payload["first_token_ms"] == 3600


def test_the_naive_app_has_no_key_no_limit_and_no_fallback(make_app):
    app = make_app(FakeProvider(failing={PRIMARY_MODEL}), hardened=False)
    assert app.handle_work_order("", MESSAGE).status == 502
    healthy = make_app(FakeProvider(), hardened=False)
    assert [healthy.handle_work_order("", MESSAGE).status for _ in range(30)] == [200] * 30


def test_an_operator_raises_a_limit_with_a_name_a_reason_and_a_ceiling(make_app):
    app = make_app()
    change = app.raise_limit("Dana Okafor", "maple-portal", 60, "Spring move-in week brings more messages")
    assert change["from"] == 30 and change["to"] == 60 and app.keys.by_name("maple-portal").burst == 40
    assert app.limit_changes == [change] and (app.changes_path.read_text().count("Dana Okafor") == 1)
    for operator, client, per_minute, reason in [("", "maple-portal", 90, "a good reason here"), ("Dana", "maple-portal", 90, "too short"),
                                                 ("Dana", "maple-portal", 50, "lower is not a raise"), ("Dana", "maple-portal", 9999, "past the ceiling"),
                                                 ("Dana", "nobody", 90, "no such client here")]:
        with pytest.raises(ValueError):
            app.raise_limit(operator, client, per_minute, reason)


def test_a_raised_limit_lets_the_flooding_client_back_in(make_app):
    from brickhaven_api.loadtest import demo_keys
    app = make_app()
    header = f"Bearer {demo_keys()['runaway-sync']}"
    for _ in range(3):
        app.handle_work_order(header, MESSAGE)
    assert app.handle_work_order(header, MESSAGE).status == 429
    app.raise_limit("Dana Okafor", "runaway-sync", 60, "Partner fixed their retry loop")
    assert app.handle_work_order(header, MESSAGE).status == 200
