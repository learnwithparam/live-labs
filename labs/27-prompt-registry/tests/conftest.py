import sys
from pathlib import Path

# The scripted models live beside the tests.
sys.path.insert(0, str(Path(__file__).parent))

import pytest

from fakes import FakeClock, FakeLangfuse, FakeOpenAI


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def registry(clock):
    """Driftwell's registry after the walkthrough's first steps: v1 from the code, v2 and v3 proposed."""
    from driftwell.naive import PROMPT_IN_CODE
    from driftwell.registry import import_from_code, propose

    client = FakeLangfuse(clock)
    import_from_code(client, PROMPT_IN_CODE)
    prompts = Path(__file__).parents[1] / "data" / "prompts"
    propose(client, (prompts / "sizing-and-export.txt").read_text(), "Add the size exchange and the data export path", "Maya Ortiz")
    propose(client, (prompts / "warmer-tone.txt").read_text(), "Warmer, shorter replies", "Sam Lee")
    return client


@pytest.fixture
def scores(registry, tmp_path):
    """Every version scored on the golden set through the real scoring code, with the recorded model."""
    from driftwell.bot import SupportBot
    from driftwell.golden import cases, score
    from driftwell.prompt_source import PromptSource
    from driftwell.registry import versions
    from driftwell.release import Scores

    saved = Scores(tmp_path / "scores.json")
    for version in versions(registry):
        bot = SupportBot(PromptSource(registry, version=version.number), FakeOpenAI(), "openai/gpt-6-luna")
        saved.put(version.text, version.number, score(cases(), lambda case: bot.reply(case["message"], case["customer"])))
    return saved
