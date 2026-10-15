"""How the running bot reads its prompt: a label move arrives within one cache window, and it never runs empty."""

import pytest
from fakes import FakeOpenAI

from driftwell.bot import SupportBot
from driftwell.naive import PROMPT_IN_CODE
from driftwell.prompt_source import PromptSource
from driftwell.registry import move_label, propose


def test_a_label_move_reaches_a_running_bot_after_the_cache_window_with_no_restart(registry, clock):
    source = PromptSource(registry, ttl_seconds=10)
    assert source.get().version == 1
    move_label(registry, 2, "production")
    clock.advance(9)
    assert source.get().version == 1, "inside the window the bot still trusts its copy"
    clock.advance(2)
    assert source.get().version == 2


def test_the_cache_spares_the_registry_a_call_per_reply(registry, clock):
    source = PromptSource(registry, ttl_seconds=10)
    for _ in range(20):
        source.get()
        clock.advance(0.4)
    assert registry.fetches == 1


def test_a_registry_that_was_never_reached_serves_the_prompt_pinned_in_the_code(registry):
    registry.down = True
    served = PromptSource(registry).get()
    assert (served.text, served.version, served.source) == (PROMPT_IN_CODE, None, "pinned fallback")


def test_an_error_after_a_good_fetch_serves_the_last_fetched_version(registry, monkeypatch):
    source = PromptSource(registry)
    assert source.get().version == 1

    def unreachable(*_, **__):
        raise ConnectionError("registry unreachable")

    monkeypatch.setattr(registry, "get_prompt", unreachable)
    served = source.get()
    assert (served.version, served.source) == (1, "last fetched")
    assert "ConnectionError" in served.reason


def test_an_empty_version_is_treated_as_an_outage_not_served(registry):
    # Saved straight through the client: `propose` itself refuses an empty prompt.
    registry.create_prompt(name="support-bot", prompt="   ", labels=["production"], commit_message="oops", config={})
    served = PromptSource(registry).get()
    assert served.source == "pinned fallback" and served.text.strip()
    assert "empty" in served.reason


def test_propose_refuses_an_empty_prompt(registry):
    with pytest.raises(ValueError):
        propose(registry, "\n  \n", "nothing", "Sam Lee")


def test_each_reply_is_traced_against_the_version_that_wrote_it(registry):
    bot = SupportBot(PromptSource(registry), FakeOpenAI(), "openai/gpt-6-luna", tracer=registry)
    answered = bot.reply("Where is my order?", "Tom")
    assert answered["version"] == 1 and registry.generations[-1]["prompt_version"] == 1
    assert answered["trace_id"] == "trace-1"


def test_a_pinned_fallback_reply_is_not_linked_to_any_version(registry):
    registry.down = True
    bot = SupportBot(PromptSource(registry), FakeOpenAI(), "openai/gpt-6-luna", tracer=registry)
    answered = bot.reply("Where is my order?", "Tom")
    assert answered["source"] == "pinned fallback" and registry.generations[-1]["prompt_version"] is None


def test_the_customer_name_is_filled_into_the_prompt(registry):
    llm = FakeOpenAI()
    SupportBot(PromptSource(registry), llm, "openai/gpt-6-luna").reply("Hi", "Priya")
    assert "Priya" in llm.calls[0][0]["content"] and "{{" not in llm.calls[0][0]["content"]
