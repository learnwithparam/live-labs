"""OpenTelemetry for the helpdesk: one tracer provider, customer and feature carried as baggage onto every
span, model calls traced by the OpenAI instrumentation with the GenAI conventions, and an exporter that
counts its failures instead of breaking a customer's request."""

import threading
from contextlib import contextmanager

from opentelemetry import baggage, context
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
from opentelemetry.processor.baggage import BaggageSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter, SpanExportResult

from quillmark.config import SERVICE_NAME

# The two keys finance joins on. Only these are copied from baggage onto spans: baggage can arrive in
# an inbound header, and an unvetted key would write whatever a caller sent into every span.
ATTRIBUTION_KEYS = ("customer.id", "feature")


class CountingExporter(SpanExporter):
    """Wraps the real exporter. A failed export is counted and reported, never raised into the request.

    The span processor already runs exports off the request thread; this makes the failure visible, so
    finance knows why some invoice lines have no trace instead of finding out at month end.
    """

    def __init__(self, inner: SpanExporter):
        self.inner, self.healthy = inner, inner
        self.exported_spans = self.failed_exports = self.lost_spans = 0
        self.lock = threading.Lock()

    def export(self, spans) -> SpanExportResult:
        try:
            result = self.inner.export(spans)
        except Exception:  # a broken collector is an operations problem, not the customer's
            result = SpanExportResult.FAILURE
        with self.lock:
            if result is SpanExportResult.SUCCESS:
                self.exported_spans += len(spans)
            else:
                self.failed_exports += 1
                self.lost_spans += len(spans)
        return result

    @contextmanager
    def pointed_at(self, exporter: SpanExporter):
        """Send to another exporter for a while; the month uses it to play a collector redeploy."""
        self.inner = exporter
        try:
            yield
        finally:
            self.inner = self.healthy

    def counts(self) -> dict:
        return {"exported_spans": self.exported_spans, "failed_exports": self.failed_exports,
                "lost_spans": self.lost_spans}

    def shutdown(self) -> None:
        self.healthy.shutdown()


class Tracing:
    """The provider, its tracer and the counting exporter, made once per process."""

    def __init__(self, exporter: SpanExporter, batch: bool = True, version: str = "2026.10"):
        resource = Resource.create({"service.name": SERVICE_NAME, "service.version": version,
                                    "deployment.environment.name": "lab"})
        self.exporter = CountingExporter(exporter)
        self.provider = TracerProvider(resource=resource)
        # Baggage is copied first, at span start, so even the instrumentation's own spans carry it.
        self.provider.add_span_processor(BaggageSpanProcessor(lambda key: key in ATTRIBUTION_KEYS))
        # Batching keeps export off the request path; tests use the simple processor to read spans at once.
        processor = BatchSpanProcessor(self.exporter) if batch else SimpleSpanProcessor(self.exporter)
        self.provider.add_span_processor(processor)
        self.tracer = self.provider.get_tracer("quillmark.agent")
        instrument_openai(self.provider)

    def flush(self) -> None:
        self.provider.force_flush()

    def shutdown(self) -> None:
        self.provider.shutdown()


_instrumented: list = []


def instrument_openai(provider: TracerProvider) -> None:
    """Patch the OpenAI client once per process: every chat call becomes a `chat <model>` span.

    The instrumentation patches the library itself, so a second provider cannot take over; tests share
    one provider for the whole session for that reason.
    """
    if not _instrumented:
        OpenAIInstrumentor().instrument(tracer_provider=provider)
        _instrumented.append(provider)


@contextmanager
def attributed_to(customer: str | None, feature: str | None):
    """Put the customer and the feature in baggage for everything this request does.

    Baggage travels with the context, into child spans, threads given the context, and outbound HTTP
    headers, so a step deep in the agent never has to be handed the customer by hand.
    """
    current = context.get_current()
    for key, value in (("customer.id", customer), ("feature", feature)):
        if value:
            current = baggage.set_baggage(key, value, current)
    token = context.attach(current)
    try:
        yield
    finally:
        context.detach(token)
