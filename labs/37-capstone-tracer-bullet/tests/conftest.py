"""Shared set-up: the slice and the naive version run once each on the scripted model, spans kept in memory."""

import sys
from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeModel  # noqa: E402

from brickhaven_slice.llm import Model  # noqa: E402
from brickhaven_slice.pipeline import Outbox, Services, run_all  # noqa: E402
from brickhaven_slice.telemetry import Tracing  # noqa: E402
from brickhaven_slice.trace_check import from_exporter  # noqa: E402
from brickhaven_slice.vendors import load_messages, load_units, load_vendors  # noqa: E402


def run_mode(mode: str, folder: Path) -> dict:
    exporter = InMemorySpanExporter()
    tracing = Tracing(exporter, batch=False)
    services = Services(Model(FakeModel(), "scripted-model"), tracing.tracer, Outbox(folder), load_units(), load_vendors(),
                        sleep=lambda _: None)
    results = run_all(services, load_messages(), f"test-{mode}", mode, workers=1)
    return {"results": results, "records": from_exporter(exporter.get_finished_spans()), "folder": folder}


@pytest.fixture(scope="session")
def slice_run(tmp_path_factory):
    return run_mode("slice", tmp_path_factory.mktemp("slice"))


@pytest.fixture(scope="session")
def naive_run(tmp_path_factory):
    return run_mode("naive", tmp_path_factory.mktemp("naive"))
