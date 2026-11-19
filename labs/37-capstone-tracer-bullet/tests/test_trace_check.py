"""The checker finds each kind of gap, and reads Jaeger's answer the same way it reads the in-memory spans."""

from dataclasses import replace

from brickhaven_slice.trace_check import from_jaeger, judge, percentile, read_back, tree_problems


def spans_of(run, request_id):
    return [record for record in run["records"] if record.attributes.get("brickhaven.request_id") == request_id]


def test_a_dropped_tool_span_is_a_gap(slice_run):
    spans = spans_of(slice_run, "m03")
    assert tree_problems(spans) == []
    without_tool = [span for span in spans if span.name != "tool match_vendor" and span.name != "tool lookup_unit"]
    assert tree_problems(without_tool) == ["step vendor_match has no tool span under it"]


def test_a_dropped_step_and_an_orphan_are_gaps(slice_run):
    spans = spans_of(slice_run, "m03")
    without_step = [span for span in spans if span.name != "step drafting"]
    problems = tree_problems(without_step)
    assert any("parent that was never exported" in problem for problem in problems)
    assert any("do not match the sent outcome" in problem for problem in problems)


def test_token_totals_that_do_not_add_up_are_a_gap(slice_run):
    spans = spans_of(slice_run, "m03")
    root = next(span for span in spans if span.name == "request")
    wrong = replace(root, attributes={**root.attributes, "gen_ai.usage.input_tokens": 1})
    problems = tree_problems([wrong if span is root else span for span in spans])
    assert problems == ["gen_ai.usage.input_tokens on the request is not the sum over its model calls"]


def test_a_request_with_no_spans_is_reported_not_skipped(slice_run):
    traces = judge(slice_run["records"], ["m03", "m99"])
    assert traces[0].complete and traces[1].problems == ["no spans reached Jaeger"]


def test_p95_is_nearest_rank():
    values = list(range(1, 21))
    assert percentile(values, 0.95) == 19 and percentile([7], 0.95) == 7


def attribute(key, value):
    return {"key": key, "value": {"stringValue": value}}


JAEGER = {"result": {"resourceSpans": [{"scopeSpans": [{"spans": [
    {"traceId": "aa", "spanId": "01", "parentSpanId": "", "name": "request", "startTimeUnixNano": "1000000", "endTimeUnixNano": "6000000",
     "attributes": [attribute("brickhaven.run_id", "run-1"), attribute("brickhaven.request_id", "m01"), attribute("brickhaven.outcome", "sent")]},
    {"traceId": "aa", "spanId": "02", "parentSpanId": "01", "name": "step intake", "startTimeUnixNano": "1100000", "endTimeUnixNano": "1300000",
     "status": {"code": 2}, "attributes": [attribute("brickhaven.run_id", "run-1"), attribute("brickhaven.request_id", "m01")]}]}]}]}}


def test_jaeger_json_becomes_the_same_records():
    records = from_jaeger(JAEGER["result"])
    assert [(record.name, record.parent_id, record.error, record.duration_us) for record in records] == [
        ("request", None, False, 5000), ("step intake", "01", True, 200)]
    assert records[0].attributes["brickhaven.outcome"] == "sent"


def test_read_back_asks_again_until_every_request_has_arrived():
    answers = [{"result": {}}, JAEGER]
    urls = []

    def fetch(url):
        urls.append(url)
        return answers.pop(0)

    records = read_back("run-1", 1, "http://jaeger", fetch=fetch, wait_seconds=5)
    assert len(records) == 2 and len(urls) == 2
    assert "/api/v3/traces" in urls[0] and "query.service_name=brickhaven-slice" in urls[0]
