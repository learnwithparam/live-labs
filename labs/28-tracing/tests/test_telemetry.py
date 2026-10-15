"""Only the attribution keys travel from baggage onto spans, and a broken exporter never raises."""

from fakes import DeadCollector
from opentelemetry import baggage, context
from opentelemetry.sdk.trace.export import SpanExportResult

from quillmark.telemetry import CountingExporter, attributed_to


def test_a_key_outside_the_attribution_keys_never_reaches_a_span(month):
    tracer = month["tracing"].tracer
    # A caller could send any baggage header; only customer.id and feature may be written to spans.
    token = context.attach(baggage.set_baggage("user.email", "ana@tidewell.example"))
    try:
        with attributed_to("tidewell", "reply-draft"), tracer.start_as_current_span("probe") as span:
            attributes = dict(span.attributes)
    finally:
        context.detach(token)
    assert attributes == {"customer.id": "tidewell", "feature": "reply-draft"}


class Exploding(DeadCollector):
    def export(self, spans):
        raise ConnectionError("collector refused the connection")


def test_an_exporter_that_raises_is_counted_not_raised():
    exporter = CountingExporter(Exploding())
    assert exporter.export(["one", "two"]) is SpanExportResult.FAILURE
    assert exporter.counts() == {"exported_spans": 0, "failed_exports": 1, "lost_spans": 2}
