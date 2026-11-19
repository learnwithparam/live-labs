"""One tracer provider for the whole slice, and the token ledger every request carries.

Spans leave the process through an exporter: OTLP to Jaeger in a real run, an in-memory list in the
keyless tests. The pipeline never knows which, so the tests read the same span tree Jaeger would hold.
"""

from dataclasses import dataclass

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter

from brickhaven_slice.config import SERVICE_NAME


class Tracing:
    """The provider and its tracer, made once per process."""

    def __init__(self, exporter: SpanExporter, batch: bool = True):
        self.provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
        # Batching keeps export off the request path; tests export each span at once so they can read it.
        processor = BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
        self.provider.add_span_processor(processor)
        self.tracer = self.provider.get_tracer("brickhaven.slice")

    def flush(self) -> None:
        """Send everything still queued. A run that exits without it loses the last spans."""
        self.provider.force_flush()

    def shutdown(self) -> None:
        self.provider.shutdown()


@dataclass
class Ledger:
    """Tokens and dollars one request spent, summed over every model call it made, retries included.

    A retried call is paid for even when it times out after the provider started work, so the ledger
    counts attempts, not answers. The root span carries these totals and a test checks they equal the
    sum over the request's `chat` spans.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_dollars: float = 0.0

    def add(self, input_tokens: int, output_tokens: int, cost_dollars: float) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_dollars += cost_dollars
