from __future__ import annotations

from typing import cast

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Histogram, generate_latest
from starlette.responses import Response


def _counter(name: str, documentation: str, *, labelnames: tuple[str, ...]) -> Counter:
    try:
        return Counter(name, documentation, labelnames=labelnames)
    except ValueError:
        collector = REGISTRY._names_to_collectors.get(name) or REGISTRY._names_to_collectors.get(
            f"{name}_total"
        )
        if collector is None:
            raise
        return cast(Counter, collector)


INGEST_EVENTS_TOTAL = _counter(
    "ingest_events_total",
    "Number of events processed by the ingest pipeline.",
    labelnames=("status",),
)

INGEST_REQUEST_DURATION_SECONDS = Histogram(
    "ingest_request_duration_seconds",
    "Ingest API request latency in seconds.",
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0),
)

INGEST_BATCH_SIZE = Histogram(
    "ingest_batch_size",
    "Number of events received per ingest request.",
    buckets=(1, 2, 5, 10, 20, 50, 100, 200, 500),
)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
