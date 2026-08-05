"""Optional OpenTelemetry tracing with W3C context propagation.

Tracing is disabled unless ``MINI_DROP_TRACING_ENABLED`` is true.  This keeps
collection and analysis deterministic when no telemetry backend is available.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator


_configured = False
_provider: Any | None = None


def tracing_enabled() -> bool:
    return os.getenv("MINI_DROP_TRACING_ENABLED", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


def configure_tracing(service_name: str, service_version: str = "0.1.0") -> bool:
    """Configure one process-wide provider; fail clearly when explicitly enabled."""

    global _configured, _provider
    if _configured:
        return True
    if not tracing_enabled():
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError as exc:
        raise RuntimeError(
            "MINI_DROP_TRACING_ENABLED=1 requires OpenTelemetry runtime dependencies"
        ) from exc

    resource = Resource.create({
        "service.name": service_name,
        "service.version": service_version,
        "deployment.environment.name": os.getenv("MINI_DROP_ENVIRONMENT", "development"),
    })
    provider = TracerProvider(resource=resource)
    exporter_name = os.getenv("MINI_DROP_TRACE_EXPORTER", "otlp").strip().lower()
    if exporter_name == "console":
        exporter = ConsoleSpanExporter()
    elif exporter_name == "otlp":
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
        if not endpoint:
            endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317").strip()
        insecure = endpoint.startswith("http://") or os.getenv(
            "OTEL_EXPORTER_OTLP_INSECURE", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
    else:
        raise RuntimeError(f"unsupported MINI_DROP_TRACE_EXPORTER: {exporter_name}")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _provider = provider
    _configured = True
    return True


def shutdown_tracing() -> None:
    global _configured, _provider
    if _provider is not None:
        _provider.shutdown()
    _provider = None
    _configured = False


def current_trace_carrier() -> dict[str, str]:
    if not tracing_enabled():
        return {}
    try:
        from opentelemetry.propagate import inject
    except ImportError:
        return {}
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


def traceparent_from_current() -> str:
    return current_trace_carrier().get("traceparent", "")


def trace_id_from_current() -> str:
    if not tracing_enabled():
        return ""
    try:
        from opentelemetry import trace
    except ImportError:
        return ""
    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else ""


def _carrier(traceparent: str | None) -> dict[str, str]:
    value = (traceparent or "").strip()
    return {"traceparent": value} if value else {}


@contextmanager
def start_span(
    name: str,
    *,
    traceparent: str | None = None,
    attributes: dict[str, Any] | None = None,
    kind: str = "internal",
    link_only: bool = False,
) -> Iterator[Any]:
    """Start a span or yield a no-op object when tracing is disabled."""

    if not tracing_enabled():
        yield None
        return
    try:
        from opentelemetry import propagate, trace
        from opentelemetry.trace import Link, SpanKind
    except ImportError:
        yield None
        return

    remote_context = propagate.extract(_carrier(traceparent)) if traceparent else None
    links = []
    parent_context = remote_context
    if link_only and remote_context is not None:
        linked = trace.get_current_span(remote_context).get_span_context()
        if linked.is_valid:
            links.append(Link(linked))
        parent_context = None
    span_kind = {
        "server": SpanKind.SERVER,
        "client": SpanKind.CLIENT,
        "producer": SpanKind.PRODUCER,
        "consumer": SpanKind.CONSUMER,
        "internal": SpanKind.INTERNAL,
    }.get(kind, SpanKind.INTERNAL)
    tracer = trace.get_tracer("mini-drop")
    with tracer.start_as_current_span(
        name,
        context=parent_context,
        kind=span_kind,
        attributes=_clean_attributes(attributes or {}),
        links=links,
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            from opentelemetry.trace import Status, StatusCode
            span.set_status(Status(StatusCode.ERROR))
            raise


def _clean_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            result[str(key)] = value
        else:
            result[str(key)] = str(value)
    return result
