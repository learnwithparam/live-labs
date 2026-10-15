import sys
from pathlib import Path

import pytest

# The scripted models live beside the tests.
sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeOpenAI  # noqa: E402
from larkspur import inventory  # noqa: E402
from larkspur.auditlog import AuditLog  # noqa: E402
from larkspur.config import APP_LOGS, AUDIT_DB, DATA  # noqa: E402
from larkspur.services import replay  # noqa: E402

KEY = b"test-pseudonym-key"


@pytest.fixture
def fortnight(tmp_path):
    """The fortnight replayed through the gateway on the scripted model, laid out as `govern replay` leaves it."""
    out_dir = tmp_path / "out"
    systems, client = inventory.load(), FakeOpenAI()
    log = AuditLog(out_dir / AUDIT_DB.name)
    outcomes = replay(DATA, systems, client, log, KEY, out_dir / APP_LOGS.name)
    yield {"out": out_dir, "systems": systems, "client": client, "log": log, "outcomes": outcomes,
           "db": out_dir / AUDIT_DB.name, "app_logs": out_dir / APP_LOGS.name}
    log.close()
