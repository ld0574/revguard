"""OTLP traces and correlation, independent of the business database."""
from __future__ import annotations

import functools
import os
from contextlib import contextmanager

from opentelemetry import context, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

_provider = TracerProvider(resource=Resource.create({"service.name": "revguard-api"}))
_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
if _endpoint:
    _provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=_endpoint, timeout=2),
        max_queue_size=2048, max_export_batch_size=128, schedule_delay_millis=1000,
    ))
_tracer = _provider.get_tracer("revguard", "0.5.0")


def carrier() -> dict:
    result = {}
    inject(result)
    return result


def log_fields() -> dict:
    span = trace.get_current_span().get_span_context()
    if not span.is_valid:
        return {}
    return {"trace_id": f"{span.trace_id:032x}", "span_id": f"{span.span_id:016x}"}


def start(name: str, *, attributes: dict | None = None, incoming: dict | None = None):
    parent = extract(incoming) if incoming and incoming.get("traceparent") else context.get_current()
    span = _tracer.start_span(name, context=parent, attributes=attributes or {})
    token = context.attach(trace.set_span_in_context(span, parent))
    return span, token


def finish(span, token, *, error: str | None = None) -> None:
    if error:
        # Error class/status is enough for exported telemetry; no raw financial
        # payload, bearer key, capability token, or exception message is exported.
        span.set_status(Status(StatusCode.ERROR, "operation failed"))
    span.end()
    context.detach(token)


@contextmanager
def operation(name: str, *, attributes: dict | None = None, incoming: dict | None = None):
    span, token = start(name, attributes=attributes, incoming=incoming)
    error = None
    try:
        yield span
    except BaseException as exc:
        error = type(exc).__name__
        span.set_attribute("error.type", error)
        raise
    finally:
        finish(span, token, error=error)


def workflow(function):
    @functools.wraps(function)
    async def wrapped(self, case, *args, **kwargs):
        with operation(function.__name__, attributes={"revguard.case.id": case["case_id"]},
                       incoming=case.get("telemetry_context")) as span:
            case["telemetry_context"] = carrier()
            self.store.save_case(case)
            result = await function(self, case, *args, **kwargs)
            run_id = (case.get("team_run") or {}).get("run_id")
            if run_id:
                span.set_attribute("revguard.run.id", run_id)
            return result
    return wrapped


def flush() -> bool:
    return _provider.force_flush(timeout_millis=5000)
