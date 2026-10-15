"""The lab's promise, on a real model and a real Jaeger: tracing attributes at least 90 percent of the
provider's invoice to a customer and a feature, logs alone attribute less, the books reconcile, and the
slowest run names its slow step. Run it with `uv run pytest -m proof -q` after starting Jaeger."""

import os

import pytest

from quillmark.cli import month, report
from quillmark.report import rebuild


@pytest.mark.proof
@pytest.mark.skipif(not os.environ.get("LLM_API_KEY"), reason="needs LLM_API_KEY")
def test_tracing_splits_the_invoice_and_names_the_slow_step():
    assert month() == 0
    assert report() == 0
    built = rebuild()
    split = built["attribution"]
    assert split["reconciles"]
    assert split["share_traced"] >= 0.9, split["share_traced"]
    assert built["logs_only"]["share"] < split["share_traced"], built["logs_only"]
    # Exactly the two lines the month plants: the untagged digest job and the run lost to the outage.
    reasons = sorted("no span" if line["span_name"] is None else "no customer" for line in split["unattributed"])
    assert reasons == ["no customer", "no span"], split["unattributed"]
    assert built["export"]["failed_exports"] >= 1
    assert built["slowest"]["customer"] == "ferrovia"
    assert built["slowest"]["slow_step"] == "execute_tool order_lookup"
