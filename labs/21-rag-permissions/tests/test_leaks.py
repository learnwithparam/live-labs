"""The suite's scoring: a leak is the document in the prompt or its secret in the answer, for a denied user."""

import asyncio
from types import SimpleNamespace

from fakes import FakeAuthorizer, FakeOpenAI

from copperline.access import Authorizer
from copperline.leaks import pairs, run_suite
from copperline.sync import documents

EVERY_DOCUMENT = {document["id"] for document in documents()}


def per_pair_table() -> FakeAuthorizer:
    """Each denied user is denied every pair document they are the denied side of."""
    table = {}
    for pair in pairs():
        table.setdefault(pair["denied"], set(EVERY_DOCUMENT)).discard(pair["document"])
    for pair in pairs():
        table.setdefault(pair["allowed"], set(EVERY_DOCUMENT))
    return FakeAuthorizer(table)


def test_a_model_that_repeats_its_sources_leaks_every_pair_without_a_check_and_none_with_one():
    suite = [pair for pair in pairs() if pair["allowed"] not in {other["denied"] for other in pairs()}]
    assert len(suite) >= 5
    # The echo model is the worst case: it repeats whatever it is shown, so only the prompt can protect.
    result = asyncio.run(run_suite(FakeOpenAI(), "fake", per_pair_table(), suite=suite))
    modes = result["score"]["modes"]
    assert modes["naive"]["leaked"] == len(suite) and modes["naive"]["in_answer"] >= 1
    assert modes["post"]["leaked"] == 0 and modes["pre"]["leaked"] == 0
    assert modes["post"]["answered_allowed"] == len(suite)


def test_a_search_that_refuses_everyone_scores_zero_answers():
    refusing = FakeOpenAI(lambda messages: "No document you can open answers this.")
    result = asyncio.run(run_suite(refusing, "fake", FakeAuthorizer({}), suite=pairs()[:3]))
    assert result["score"]["modes"]["post"]["answered_allowed"] == 0


def test_an_errored_check_is_a_denial():
    def batch_check(request):
        results = [SimpleNamespace(correlation_id=check.correlation_id, allowed=position == 0,
                                   error=None if position == 0 else "deadline exceeded")
                   for position, check in enumerate(request.checks)]
        return SimpleNamespace(result=results)

    authorizer = Authorizer.__new__(Authorizer)
    authorizer.fga = SimpleNamespace(batch_check=batch_check)
    assert authorizer.allowed("ravi", ["SUP-298", "SUP-311"]) == {"SUP-298"}
