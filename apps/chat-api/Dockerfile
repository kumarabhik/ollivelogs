# OlliveLogs — chat-api image
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

# Install deps first for better layer caching.
COPY apps/chat-api/pyproject.toml /app/apps/chat-api/pyproject.toml
RUN pip install --upgrade pip \
    && pip install --no-cache-dir /app/apps/chat-api

# Copy migrations + app sources + pricing config.
COPY alembic.ini /app/alembic.ini
COPY db /app/db
COPY apps/chat-api /app/apps/chat-api
COPY packages/ollivelogs-py /app/packages/ollivelogs-py
COPY infra/pricing.yaml /app/infra/pricing.yaml

ENV PYTHONPATH=/app:/app/apps/chat-api:/app/packages/ollivelogs-py

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--app-dir", "/app/apps/chat-api"]
