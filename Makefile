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
up: $(UP_DEPS) ## Start in docker on air-net (INFRA=none: own network), seeded from sit/corpus (AIR_RAG_SERVICE_SEED_DIR= for an empty index)
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

# ---- image and deployment (UAT and production) ------------------------------
# This repository owns its Dockerfile and image. The Helm chart, the Terraform
# and the deployment scripts for every core service live in
# air-infrastructure-service (gke/), where DevOps keeps them. The targets below
# build and push the image here, then hand over to that repository, checked out
# next to this one (INFRA_DIR). Its docs/03-gke-runbook.md is the runbook.
#
#   DEPLOY_ENV        uat | prod
#   IMAGE_REPOSITORY  <region>-docker.pkg.dev/<project>/air/air-rag-service
#   IMAGE_TAG         defaults to this checkout's short commit
#   IMAGE_DIGEST      what CI should deploy: a digest can't be re-pointed
#   KUBE_CONTEXT      required by deploy / deploy-smoke; never defaulted, so a
#                     deploy can't land on whatever cluster happens to be current
#   GCP_PROJECT       the environment's project (Secret Manager)
#   KEYS_FILE         deploy-smoke only, optional: an env file of raw API keys,
#                     to add one authenticated call to the readiness checks
DEPLOY_ENV ?= uat
SERVICE    := air-rag-service
IMAGE_TAG  ?= $(shell git rev-parse --short=12 HEAD 2>/dev/null || echo dev)
INFRA_MAKE  = $(MAKE) --no-print-directory -C $(INFRA_DIR) SERVICE=$(SERVICE) DEPLOY_ENV=$(DEPLOY_ENV) \
	KUBE_CONTEXT="$(KUBE_CONTEXT)" GCP_PROJECT="$(GCP_PROJECT)" KEYS_FILE="$(KEYS_FILE)" \
	IMAGE_REPOSITORY="$(IMAGE_REPOSITORY)" IMAGE_DIGEST="$(IMAGE_DIGEST)" \
	IMAGE_TAG="$(if $(IMAGE_DIGEST),,$(IMAGE_TAG))"

.PHONY: require-infra-repo
require-infra-repo:
	@test -f $(INFRA_DIR)/gke/scripts/release.sh || { \
		echo "air-infrastructure-service is not at $(INFRA_DIR): the charts and deploy scripts live there."; \
		echo "Check it out next to this repository, or pass INFRA_DIR=<path>."; exit 1; }

.PHONY: require-image-repository
require-image-repository:
	@test -n "$(IMAGE_REPOSITORY)" || { echo "set IMAGE_REPOSITORY, e.g. <region>-docker.pkg.dev/<project>/air/$(SERVICE)"; exit 1; }

.PHONY: image
image: ## Build the image locally as $(SERVICE):$(IMAGE_TAG)
	docker build -t $(SERVICE):$(IMAGE_TAG) .

.PHONY: image-push
image-push: require-image-repository ## Build for linux/amd64 and push IMAGE_REPOSITORY:IMAGE_TAG (gcloud auth configure-docker first)
	docker build --platform linux/amd64 -t $(IMAGE_REPOSITORY):$(IMAGE_TAG) .
	docker push $(IMAGE_REPOSITORY):$(IMAGE_TAG)

.PHONY: deploy-render
deploy-render: require-infra-repo ## Print the manifests DEPLOY_ENV would get (SAMPLE=1 fills the project placeholders)
	@$(INFRA_MAKE) SAMPLE="$(SAMPLE)" gke-render

.PHONY: deploy
deploy: require-infra-repo ## Deploy this service to DEPLOY_ENV (uat|prod) in KUBE_CONTEXT: helm upgrade --install
	@$(INFRA_MAKE) gke-deploy

.PHONY: deploy-smoke
deploy-smoke: require-infra-repo ## Smoke-test this service in KUBE_CONTEXT: rollout, health, readiness (KEYS_FILE adds a real call)
	@$(INFRA_MAKE) gke-smoke

.PHONY: clean
clean: ## Remove the venv and tool caches
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
