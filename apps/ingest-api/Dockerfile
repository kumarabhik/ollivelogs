# OlliveLogs - ingest-api image
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential curl libpq-dev wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY apps/ingest-api/pyproject.toml /app/apps/ingest-api/pyproject.toml
RUN pip install --upgrade pip \
    && pip install --no-cache-dir /app/apps/ingest-api

COPY apps/ingest-api /app/apps/ingest-api

ENV PYTHONPATH=/app:/app/apps/ingest-api

EXPOSE 8002

CMD ["uvicorn", "ingest_app.main:app", "--host", "0.0.0.0", "--port", "8002", "--app-dir", "/app/apps/ingest-api"]
