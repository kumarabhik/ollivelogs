# OlliveLogs - log-consumer image
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

COPY workers/log-consumer/pyproject.toml /app/workers/log-consumer/pyproject.toml
RUN pip install --upgrade pip \
    && pip install --no-cache-dir /app/workers/log-consumer

COPY workers/log-consumer /app/workers/log-consumer

ENV PYTHONPATH=/app:/app/workers/log-consumer

EXPOSE 8003

CMD ["uvicorn", "log_consumer_app.main:app", "--host", "0.0.0.0", "--port", "8003", "--app-dir", "/app/workers/log-consumer"]
