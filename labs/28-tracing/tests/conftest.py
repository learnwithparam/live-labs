import sys
from pathlib import Path

import pytest

# The scripted provider and the in-memory month live beside the tests.
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(scope="session")
def month():
    """One month on the scripted provider, shared: the OpenAI instrumentation patches the library once."""
    from fakes import run_fake_month

    return run_fake_month()
