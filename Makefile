VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
PYTEST  := $(VENV)/bin/pytest
UVICORN := $(VENV)/bin/uvicorn
COMPOSE ?= docker compose

HOST ?= 0.0.0.0
PORT ?= 8087

SOURCES := src tests scripts
SIT_CORPUS := sit/corpus

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@echo "air-rag — make <target>"
	@echo
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Overridable: HOST=$(HOST) PORT=$(PORT)"
	@echo "Needs air-model-gateway on :8083 — see ../air-model-gateway (make up)."

# ---- dev inner loop --------------------------------------------------------
$(PY):
	python3.12 -m venv $(VENV)

.PHONY: install
install: $(PY) ## Create .venv and install the package
	$(PIP) install --upgrade pip
	$(PIP) install -e "."

.PHONY: dev
dev: install ## Add the development toolchain (pytest, ruff, mypy)
	$(PIP) install -e ".[dev]"

.PHONY: env
env: ## Create .env from .env.example, if absent
	@if [ -f .env ]; then \
		echo ".env already exists — leaving it alone."; \
	else \
		cp .env.example .env; \
		echo "Wrote .env."; \
	fi

.PHONY: run
run: ## Run the service with reload (needs air-model-gateway on :8083)
	$(UVICORN) air_rag.main:app --reload --host $(HOST) --port $(PORT)

.PHONY: run-sit
run-sit: ## Run the service seeded with the SIT corpus (sit/corpus)
	AIR_RAG__INGESTION__SEED_DIR=$(SIT_CORPUS) $(UVICORN) air_rag.main:app --host $(HOST) --port $(PORT)

.PHONY: sit-check
sit-check: ## Run sit/queries.json against a seeded service on :$(PORT)
	$(PY) scripts/sit_check.py --base-url http://localhost:$(PORT)

.PHONY: test
test: ## Run tests
	$(PYTEST)

.PHONY: lint
lint: ## Ruff lint
	$(PY) -m ruff check $(SOURCES)

.PHONY: fmt
fmt: ## Ruff format and autofix
	$(PY) -m ruff format $(SOURCES)
	$(PY) -m ruff check --fix $(SOURCES)

.PHONY: typecheck
typecheck: ## Mypy (strict)
	$(PY) -m mypy src

.PHONY: check
check: lint typecheck test ## Lint + typecheck + test

.PHONY: openapi
openapi: ## Write the OpenAPI document to docs/openapi.json
	$(PY) -c "import json, pathlib; from air_rag.main import create_app; \
		pathlib.Path('docs/openapi.json').write_text(json.dumps(create_app().openapi(), indent=2) + '\n')"
	@echo "Wrote docs/openapi.json"

# ---- container ---------------------------------------------------------------
.PHONY: up
up: ## Start this service in docker (needs air-model-gateway reachable at host.docker.internal:8083)
	$(COMPOSE) up -d --build
	@$(MAKE) --no-print-directory status

.PHONY: status
status: ## Report this service's own readiness
	@sleep 2
	@printf 'air-rag: http://localhost:$(PORT)/v1/health\n'
	@code=$$(curl -s -o /dev/null -w '%{http_code}' http://localhost:$(PORT)/v1/ready 2>/dev/null); \
	if [ "$$code" = "200" ]; then \
		printf 'ready:   yes — air-model-gateway serving the embedding model\n'; \
	elif [ "$$code" = "503" ]; then \
		printf 'ready:   NO (503) — see the detail: curl -s localhost:$(PORT)/v1/ready (gateway: cd ../air-model-gateway && make up)\n'; \
	else \
		printf 'ready:   could not probe (curl said "%s") — try: make logs\n' "$$code"; \
	fi

.PHONY: down
down: ## Stop the container
	$(COMPOSE) down

.PHONY: logs
logs: ## Tail container logs
	$(COMPOSE) logs -f air-rag

.PHONY: clean
clean: ## Remove the venv and tool caches
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
