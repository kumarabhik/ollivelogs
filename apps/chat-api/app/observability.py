"""OTel + Prometheus bootstrap for chat-api.

Soft init: if OTEL_EXPORTER_OTLP_ENDPOINT is unset, this is a no-op so local
dev keeps working without the Collector. Metrics in DESIGN.md §7 are
declared here so any module can import and use them.

Call `install_observability(app)` once during app startup.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Final, Protocol, cast

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.responses import Response

from app.inference_logging import current_traceparent
from app.request_context import current_request_id

log = logging.getLogger("observability")

SERVICE_NAME: Final = "chat-api"


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

# ─── Prometheus metrics (per DESIGN.md §7) ───
LLM_REQUEST_TOTAL = Counter(
    "llm_request_total",
    "LLM provider calls by provider, model, and status.",
    labelnames=("provider", "model", "status"),
)
LLM_LATENCY_MS = Histogram(
    "llm_latency_ms",
    "End-to-end LLM call latency in milliseconds.",
    labelnames=("provider", "model"),
    buckets=(50, 100, 200, 400, 800, 1500, 3000, 6000, 12000, 30000),
)
LLM_TTFT_MS = Histogram(
    "llm_ttft_ms",
    "Time-to-first-token (streaming) in milliseconds.",
    labelnames=("provider", "model"),
    buckets=(50, 100, 200, 400, 800, 1500, 3000, 6000),
)
LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "Tokens consumed by kind (prompt|completion).",
    labelnames=("provider", "model", "kind"),
)
LLM_COST_USD_TOTAL = Counter(
    "llm_cost_usd_total",
    "Cumulative provider spend in USD.",
    labelnames=("provider", "model"),
)
ACTIVE_STREAMS = Gauge(
    "chat_active_streams",
    "Currently open SSE streams.",
)


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
        traceparent = current_traceparent()
        if traceparent is not None:
            payload["traceparent"] = traceparent
        for key in ("method", "path", "status_code", "duration_ms", "endpoint", "reason"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


def metrics_response() -> Response:
    """Mount this on `GET /metrics`."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


def install_observability(app: object) -> None:
    """Wire OTel tracing for FastAPI if OTLP endpoint is configured.

    Safe to call when the OTel SDK isn't installed — logs a warning and skips.
    """
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
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
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
    HTTPXClientInstrumentor().instrument()
    _configure_root_logging(
        endpoint=endpoint,
        resource=resource,
        logger_provider_factory=cast(_LoggerProviderFactory, LoggerProvider),
        log_exporter_factory=cast(_LogExporterFactory, OTLPLogExporter),
        log_processor_factory=cast(_LogProcessorFactory, BatchLogRecordProcessor),
        logging_handler_factory=cast(_LoggingHandlerFactory, LoggingHandler),
    )

    log.info("otel.enabled", extra={"endpoint": endpoint, "service": SERVICE_NAME})


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
