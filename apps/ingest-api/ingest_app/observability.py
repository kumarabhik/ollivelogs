"""OTel bootstrap for ingest-api. Soft no-op if OTLP endpoint not configured."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Final, Protocol, cast

from ingest_app.request_context import current_request_id

log = logging.getLogger("observability")
SERVICE_NAME: Final = "ingest-api"


class _LoggerProviderLike(Protocol):
    def add_log_record_processor(self, processor: object) -> None: ...


class _LoggerProviderFactory(Protocol):
    def __call__(self, *, resource: object) -> _LoggerProviderLike: ...


class _LogExporterFactory(Protocol):
    def __call__(self, *, endpoint: str, insecure: bool) -> object: ...


class _LogProcessorFactory(Protocol):
    def __call__(self, exporter: object) -> object: ...


class _LoggingHandlerFactory(Protocol):
    def __call__(self, *, level: int, logger_provider: object) -> logging.Handler: ...


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "service": SERVICE_NAME,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or current_request_id()
        if isinstance(request_id, str) and request_id != "":
            payload["request_id"] = request_id
        traceparent = _current_traceparent()
        if traceparent is not None:
            payload["traceparent"] = traceparent
        for key in ("method", "path", "status_code", "duration_ms", "reason"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


def install_observability(app: object) -> None:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        log.info("otel.disabled", extra={"reason": "OTEL_EXPORTER_OTLP_ENDPOINT unset"})
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        log.warning("otel.skip", extra={"reason": f"opentelemetry not installed: {exc}"})
        return

    resource = Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.namespace": os.getenv("OTEL_SERVICE_NAMESPACE", "ollivelogs"),
            "deployment.environment": os.getenv("APP_ENV", "local"),
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
    AsyncPGInstrumentor().instrument()
    RedisInstrumentor().instrument()
    _configure_root_logging(
        endpoint=endpoint,
        resource=resource,
        logger_provider_factory=cast(_LoggerProviderFactory, LoggerProvider),
        log_exporter_factory=cast(_LogExporterFactory, OTLPLogExporter),
        log_processor_factory=cast(_LogProcessorFactory, BatchLogRecordProcessor),
        logging_handler_factory=cast(_LoggingHandlerFactory, LoggingHandler),
    )

    log.info("otel.enabled", extra={"endpoint": endpoint, "service": SERVICE_NAME})


def _current_traceparent() -> str | None:
    try:
        from opentelemetry import trace
        from opentelemetry.trace.span import format_span_id, format_trace_id
    except ImportError:
        return None

    span = trace.get_current_span()
    span_context = span.get_span_context()
    if not span_context.is_valid:
        return None
    trace_flags = int(span_context.trace_flags) & 0x01
    return (
        f"00-{format_trace_id(span_context.trace_id)}-"
        f"{format_span_id(span_context.span_id)}-{trace_flags:02x}"
    )


def _configure_root_logging(
    *,
    endpoint: str,
    resource: object,
    logger_provider_factory: _LoggerProviderFactory,
    log_exporter_factory: _LogExporterFactory,
    log_processor_factory: _LogProcessorFactory,
    logging_handler_factory: _LoggingHandlerFactory,
) -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        for handler in root_logger.handlers:
            handler.setFormatter(JsonLogFormatter())
    else:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(JsonLogFormatter())
        root_logger.addHandler(stream_handler)

    if any(handler.get_name() == "otlp-logs" for handler in root_logger.handlers):
        return

    logger_provider = logger_provider_factory(resource=resource)
    logger_provider.add_log_record_processor(
        log_processor_factory(log_exporter_factory(endpoint=endpoint, insecure=True))
    )
    otlp_handler = logging_handler_factory(level=logging.INFO, logger_provider=logger_provider)
    otlp_handler.set_name("otlp-logs")
    root_logger.addHandler(otlp_handler)
