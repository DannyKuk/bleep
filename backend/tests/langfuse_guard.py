"""Keeps a Langfuse client built by one test from exporting spans into the next.

Langfuse keeps one client per public key for the whole process and adds a span processor for it to
the process-wide OpenTelemetry provider. A test that builds a tracer and returns leaves both
behind, so the Anthropic SDK's spans from a later test (made over a mock transport) reach its
OTLP exporter, which resolves the tracer's host and logs an ERROR into whatever test runs then
(#602). The exporter's network call is therefore replaced for the whole process, and every test
shuts its Langfuse clients and their span processors down when it ends. Shutting a client down
alone is not enough: its processor stays on the provider and keeps exporting.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from unittest import mock

from langfuse._client.resource_manager import LangfuseResourceManager
from langfuse._client.span_processor import LangfuseSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import SpanExportResult

_listening: list[list[str]] = []


def _record(self: OTLPSpanExporter, spans: object) -> SpanExportResult:
    if _listening:
        _listening[-1].append(self._endpoint)
    return SpanExportResult.SUCCESS


OTLPSpanExporter.export = _record  # type: ignore[method-assign]


class LeakedExporter(AssertionError):
    """Spans reached a Langfuse exporter that would have sent them over the network."""


@contextmanager
def langfuse_exports_blocked() -> Iterator[list[str]]:
    """The list holds the endpoint of every batch an OTLP exporter was given meanwhile (the
    innermost use of this manager gets them). On exit every Langfuse client and span processor
    made inside is shut down, pending spans are flushed into the list first, and a batch in it
    fails the test."""
    sent: list[str] = []
    processors: list[LangfuseSpanProcessor] = []
    make_processor = LangfuseSpanProcessor.__init__

    def track_processor(self: LangfuseSpanProcessor, *args: object, **kwargs: object) -> None:
        make_processor(self, *args, **kwargs)
        processors.append(self)

    _listening.append(sent)
    with mock.patch.object(LangfuseSpanProcessor, "__init__", track_processor):
        try:
            yield sent
        finally:
            LangfuseResourceManager.reset()
            for processor in processors:
                processor.shutdown()
            _listening.remove(sent)
    if sent:
        raise LeakedExporter(
            f"a Langfuse exporter received spans and would have sent them to {sorted(set(sent))}: "
            "a test left a tracer running (#602)"
        )
