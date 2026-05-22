# OlliveLogs — developer entrypoints
# Run `make help` to list targets.

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose
PY_APPS := apps/chat-api apps/ingest-api workers/log-consumer packages/ollivelogs-py

# ---------- meta ----------
.PHONY: help
help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ---------- lifecycle ----------
.PHONY: dev
dev: ## Bring up full stack (postgres, redis, clickhouse, grafana, apps).
	$(COMPOSE) up -d --build
	@echo "→ web      http://localhost:$${WEB_PORT:-3000}"
	@echo "→ chat     http://localhost:$${CHAT_API_PORT:-8001}/docs"
	@echo "→ ingest   http://localhost:$${INGEST_API_PORT:-8002}/docs"
	@echo "→ grafana  http://localhost:$${GRAFANA_PORT:-3001}"

.PHONY: down
down: ## Stop everything.
	$(COMPOSE) down

.PHONY: nuke
nuke: ## Stop and delete all volumes (DESTRUCTIVE).
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail logs from all services.
	$(COMPOSE) logs -f --tail=200

.PHONY: ps
ps: ## Show container status.
	$(COMPOSE) ps

# ---------- db ----------
.PHONY: migrate
migrate: ## Apply Postgres migrations.
	$(COMPOSE) exec chat-api alembic upgrade head

.PHONY: seed
seed: ## Seed Postgres with demo users/conversations.
	$(COMPOSE) exec chat-api python -m db.seed

# ---------- quality ----------
.PHONY: lint
lint: ## Lint all code (ruff + eslint).
	ruff check $(PY_APPS)
	cd apps/web && npm run lint
	cd packages/ollivelogs-js && npm run lint

.PHONY: fmt
fmt: ## Auto-format.
	ruff format $(PY_APPS)
	cd apps/web && npm run fmt
	cd packages/ollivelogs-js && npm run fmt

.PHONY: typecheck
typecheck: ## Run mypy + tsc.
	mypy $(PY_APPS)
	cd apps/web && npx tsc --noEmit
	cd packages/ollivelogs-js && npx tsc --noEmit

.PHONY: test
test: ## Run unit + integration tests.
	pytest -q tests/unit tests/integration

.PHONY: test-e2e
test-e2e: ## Run Playwright e2e (requires `make dev` running).
	cd tests/e2e && npx playwright test

.PHONY: load
load: ## Run k6 load test.
	k6 run tests/load/chat-load.js

# ---------- build & push ----------
.PHONY: build
build: ## Build all docker images.
	$(COMPOSE) build

.PHONY: push
push: ## Push images to registry (set IMAGE_PREFIX).
	@: $${IMAGE_PREFIX:?need IMAGE_PREFIX, e.g. ghcr.io/abhi/ollive}
	$(COMPOSE) push
