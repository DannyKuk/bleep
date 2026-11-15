import uuid

import pytest
from langfuse import Langfuse
from opentelemetry import trace

from tests.langfuse_guard import LeakedExporter, langfuse_exports_blocked


def build_client() -> Langfuse:
    return Langfuse(
        public_key=f"pk-lf-{uuid.uuid4().hex}",
        secret_key="sk-lf-test-secret",
        base_url="https://cloud.example.test",
    )


def emit_sdk_span() -> None:
    """What the Anthropic SDK does on every call it makes, over a mock transport or not."""
    tracer = trace.get_tracer("com.anthropic.sdk.python")
    with tracer.start_as_current_span(
        "anthropic.messages.create", attributes={"gen_ai.provider.name": "anthropic"}
    ):
        pass


def test_a_span_reaching_a_langfuse_exporter_fails_the_test_instead_of_the_network():
    with pytest.raises(LeakedExporter, match="cloud.example.test"), langfuse_exports_blocked():
        build_client()
        emit_sdk_span()


def test_a_client_from_an_earlier_test_receives_nothing_from_a_later_one():
    """The #602 leak: the second test's spans went to the first one's exporter."""
    with langfuse_exports_blocked():
        build_client()
    with langfuse_exports_blocked() as sent:
        emit_sdk_span()
    assert sent == []
