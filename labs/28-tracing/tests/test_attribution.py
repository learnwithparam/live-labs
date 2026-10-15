"""The invoice splits by customer and feature from the traces, and every cent lands in exactly one place."""

import pytest

from quillmark.attribution import attribute, attribute_from_logs, runs, waterfall


def test_baggage_puts_the_customer_on_the_instrumentation_s_own_chat_spans(month):
    chats = [span for span in month["spans"] if span["attributes"].get("gen_ai.operation.name") == "chat"]
    assert chats, "the OpenAI instrumentation made no chat spans"
    customers = {span["attributes"].get("customer.id") for span in chats}
    assert {"brightwater", "ferrovia", "larkspur", "tidewell"} <= customers
    # The GenAI conventions name the provider's response id; that is the join to the invoice.
    assert all(span["attributes"]["gen_ai.response.id"].startswith("gen-fake-") for span in chats)
    assert all(span["attributes"]["gen_ai.request.model"] == "openai/gpt-6-luna" for span in chats)


def test_every_invoice_line_lands_in_one_cell_or_the_unattributed_bucket(month):
    split = attribute(month["lines"], month["spans"])
    placed = sum(cell["lines"] for cell in split["cells"]) + len(split["unattributed"])
    assert placed == len(month["lines"]) == split["invoice_lines"]
    assert split["reconciles"]
    assert split["traced_cost"] + split["unattributed_cost"] == pytest.approx(split["invoice_total"], abs=1e-12)
    assert 0.8 < split["share_traced"] < 1.0


def test_a_call_with_no_customer_is_never_spread_across_customers(month):
    split = attribute(month["lines"], month["spans"])
    no_customer = [line for line in split["unattributed"] if line["span_name"]]
    assert len(no_customer) == 1
    assert no_customer[0]["run_name"] == "invoke_agent weekly-digest"
    assert "no customer.id or feature" in no_customer[0]["reason"]
    # The digest's cost is nowhere in the customer cells: it stays on its own line.
    assert all(cell["feature"] != "weekly-digest" for cell in split["cells"])


def test_a_billed_call_whose_span_was_lost_says_so(month):
    split = attribute(month["lines"], month["spans"])
    lost = [line for line in split["unattributed"] if not line["span_name"]]
    assert len(lost) == 1 and lost[0]["trace_id"] is None
    assert "never reached the collector" in lost[0]["reason"]


def test_finance_assigns_one_line_and_flags_the_other(month):
    unattributed = attribute(month["lines"], month["spans"])["unattributed"]
    digest, lost = sorted(unattributed, key=lambda line: line["span_name"] is None)
    decisions = {digest["generation_id"]: {"customer": "tidewell", "feature": "weekly-digest"},
                 lost["generation_id"]: {"flag": True}}
    split = attribute(month["lines"], month["spans"], decisions)
    assert [line["generation_id"] for line in split["unattributed"]] == [lost["generation_id"]]
    assert split["unattributed"][0]["flag"] is True
    cell = next(cell for cell in split["cells"] if cell["feature"] == "weekly-digest")
    assert cell["customer"] == "tidewell" and cell["by_finance"] == pytest.approx(digest["cost"])
    # Finance's share is reported apart from tracing's, so the metric never takes credit for a person.
    assert split["share_after_review"] > split["share_traced"]
    assert split["reconciles"]


def test_logs_alone_attribute_less_than_the_traces_when_requests_overlap(month):
    traced = attribute(month["lines"], month["spans"])["share_traced"]
    logs = attribute_from_logs(month["lines"], month["events"])
    assert logs["ambiguous_lines"] >= 1
    assert logs["share"] < traced


def test_the_slowest_run_is_ferrovia_and_its_slow_step_is_the_order_lookup(month):
    slowest = runs(month["spans"])[0]
    assert slowest["customer"] == "ferrovia" and slowest["feature"] == "reply-draft"
    steps = waterfall(month["spans"], slowest["trace_id"], month["lines"])
    assert steps["slow_step"] == "execute_tool order_lookup"
    names = [row["name"] for row in steps["rows"]]
    assert names[0] == "invoke_agent reply-draft" and "retrieve help_center" in names
    # Each model call on the waterfall carries the cost the provider billed for it.
    assert sum(1 for row in steps["rows"] if row["cost"]) == 2


def test_a_dead_collector_is_counted_and_the_customer_still_gets_an_answer(month):
    result = month["result"]
    assert all(run["ok"] for run in result["runs"])
    assert result["export"]["failed_exports"] >= 1 and result["export"]["lost_spans"] >= 1
    outage = next(run for run in result["runs"] if run.get("collector"))
    assert outage["run"] == "R-17" and outage["reply"]


def test_a_span_with_a_customer_but_no_feature_is_not_billed_to_a_half_cell():
    """Half an owner is no owner: finance cannot charge a customer for a feature nobody named."""
    span = {"trace_id": "t1", "span_id": "s1", "parent_id": None, "name": "chat", "attributes": {"gen_ai.response.id": "gen-1", "customer.id": "ferrovia"}}
    split = attribute([{"generation_id": "gen-1", "cost": 0.002}], [span])
    assert split["cells"] == [] and split["share_traced"] == 0.0
    assert "feature" in split["unattributed"][0]["reason"]
