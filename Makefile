VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
PYTEST  := $(VENV)/bin/pytest
UVICORN := $(VENV)/bin/uvicorn
COMPOSE ?= docker compose

HOST ?= 0.0.0.0
PORT ?= 8087

SOURCES := src tests scripts

INFRA_DIR ?= ../air-infrastructure-service
NETWORK   := air-net

# Container runs only: `shared` (default) joins the platform's air-net and
# refuses to start without it; `none` runs on this project's own network.
INFRA ?= shared
ifeq ($(INFRA),shared)
COMPOSE_FILES := -f docker-compose.yml
UP_DEPS := infra-check
else ifeq ($(INFRA),none)
COMPOSE_FILES := -f docker-compose.yml -f docker-compose.standalone.yml
UP_DEPS :=
else
$(error INFRA must be `shared` or `none`, got `$(INFRA)`)
endif
SIT_CORPUS := sit/corpus

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@echo "air-rag-service — make <target>"
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
	$(UVICORN) air_rag_service.main:app --reload --host $(HOST) --port $(PORT)

.PHONY: run-sit
run-sit: ## Run the service seeded with the SIT corpus (sit/corpus)
	AIR_RAG_SERVICE__INGESTION__SEED_DIR=$(SIT_CORPUS) $(UVICORN) air_rag_service.main:app --host $(HOST) --port $(PORT)

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
	$(PY) -c "import json, pathlib; from air_rag_service.main import create_app; \
		pathlib.Path('docs/openapi.json').write_text(json.dumps(create_app().openapi(), indent=2) + '\n')"
	@echo "Wrote docs/openapi.json"

# ---- container ---------------------------------------------------------------
.PHONY: infra-check
infra-check: ## Fail early if air-infra's shared network is not up
	@docker network inspect $(NETWORK) >/dev/null 2>&1 || { \
		echo "air-infra is not up: network '$(NETWORK)' does not exist."; \
		echo; \
		echo "    cd $(INFRA_DIR) && make up"; \
		echo; \
		echo "Or run this service on its own network:"; \
		echo; \
		echo "    make up INFRA=none"; \
		echo; \
		exit 1; }

.PHONY: up
up: $(UP_DEPS) ## Start in docker on air-net (INFRA=none: own network; AIR_RAG_SERVICE_SEED_DIR=/app/sit/corpus: seed SIT)
	$(COMPOSE) $(COMPOSE_FILES) up -d --build
	@$(MAKE) --no-print-directory status

.PHONY: status
status: ## Report this service's own readiness
	@printf 'air-rag-service: http://localhost:$(PORT)/v1/health (on air-net as http://air-rag-service:8080)\n'
	@# Startup seeding can take a while; wait for the process to answer at all.
	@for i in $$(seq 1 60); do curl -sf -m 1 -o /dev/null http://localhost:$(PORT)/v1/health && break; sleep 1; done
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
	$(COMPOSE) $(COMPOSE_FILES) down --remove-orphans

.PHONY: logs
logs: ## Tail container logs
	$(COMPOSE) $(COMPOSE_FILES) logs -f air-rag-service

.PHONY: clean
clean: ## Remove the venv and tool caches
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
