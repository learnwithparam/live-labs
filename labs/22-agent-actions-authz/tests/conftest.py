import sys
from pathlib import Path

# The scripted models live beside the tests.
sys.path.insert(0, str(Path(__file__).parent))

import pytest

from wrenfield import pdp
from wrenfield.config import TEST_PDP_GRPC_PORT, TEST_PDP_HTTP_PORT
from wrenfield.policy import Policy


@pytest.fixture(scope="session")
def cerbos():
    """One real Cerbos for the whole run, on its own ports, so a running page never answers for the tests."""
    with pdp.running(TEST_PDP_HTTP_PORT, TEST_PDP_GRPC_PORT) as client:
        yield client


@pytest.fixture
def policy(cerbos):
    return Policy(cerbos)
