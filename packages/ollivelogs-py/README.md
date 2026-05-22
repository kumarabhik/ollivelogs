# ollivelogs-py

Thin Python SDK for OlliveLogs.

## Quick start

```python
from ollivelogs import OlliveLogs

ol = OlliveLogs(endpoint="http://localhost:8002/v1/logs")

with ol.trace(conversation_id="conv-123", user_id="user-123") as span:
    response = ol.wrap(openai_client).chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": "Hello"}],
    )
    span.set_output(response)

ol.flush()
ol.close()
```

## What it does

- Wraps OpenAI- and Anthropic-style Python SDK calls.
- Captures request previews, response previews, token usage, latency, and status.
- Ships events to OlliveLogs ingest in async batches using `httpx`.
- Retries failed batches with backoff and counts dropped events with Prometheus.
