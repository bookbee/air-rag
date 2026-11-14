# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

air-rag is the ingestion/indexing/retrieval service of the AIR platform (Python 3.12, FastAPI, Pydantic v2). It is a **Phase-0 skeleton**: auth, health/ready/capabilities, an in-process vector store, and a chunk → embed → index pipeline are real, but no real document source is ingested yet. `docs/00-plan.md` holds the goals, non-goals and phased roadmap. Read it before adding scope.

Sibling repos are referenced by relative path (`../air-model-gateway`, `air-platform/docs/...`). Port map: 8083 is air-model-gateway, 8087 is this service.

## Commands

```bash
make dev          # create .venv (python3.12) + install with [dev] extras
make env          # copy .env.example -> .env if absent
make run          # uvicorn with reload on :8087 (needs air-model-gateway on :8083: cd ../air-model-gateway && make up)
make check        # ruff lint + mypy --strict (src only) + pytest
make lint | fmt | typecheck | test
make openapi      # write docs/openapi.json from create_app()
make run-sit      # run seeded from sit/corpus (AIR_RAG__INGESTION__SEED_DIR)
make sit-check    # scripts/sit_check.py: sit/queries.json against a seeded :8087
make up / down / logs / status   # docker compose; container reaches air-model-gateway at host.docker.internal:8083
```

Single test: `.venv/bin/pytest tests/unit/test_retrieval_route.py::test_retrieve_requires_a_key`. pytest is configured with `asyncio_mode = "auto"` and `pythonpath = ["src"]`, so async tests need no decorator.

Local dev API key: `X-API-Key: airr_local_dev_key` (its hash is in `.env.example`). To mint a new key: `python -c "from air_rag.security.api_keys import generate_key; print(generate_key('your-salt'))"`.

## Architecture

- **App factory** (`main.py`): `create_app(settings)` builds the single `AppState` (settings, `ApiKeyStore`, `ModelGatewayClient`, `VectorStore`) and attaches it to `app.state` under `STATE_ATTR`. Routes get it through `api/deps.py:get_app_state`. Construction in `create_app` is the only place a backend is chosen. Swapping `InMemoryVectorStore` for a real backend means implementing the `VectorStore` Protocol in `retrieval/base.py` (`search`, `upsert`, `__len__`) and changing that one line.
- **Public API is read-only.** `POST /v1/retrieve` embeds the query through air-model-gateway and searches the store. No route writes to the index. Ingestion (`ingestion/base.py`: `Chunker` Protocol, naive `FixedSizeChunker`, `ingest_document`) is an internal path that no route exposes. Its only driver is startup seeding (`ingestion/seed.py`, invoked from the lifespan when `ingestion.seed_dir` is set). Seeding turns each file's front matter into chunk metadata (`title`, `uri`, `kind`), and a gateway failure stops it without blocking boot. Chunk ids are `"{source_id}#{index}"`.
- **air-model-gateway is the only model path** (`clients/model_gateway.py`), reached over HTTP with this service's token in `X-API-Key` (`air-rag-dev` locally). It calls `POST /v1/embed` with `{"model": "local-768-v1", "input": [...]}`. Unknown fields such as the old `task` get a 422, and `/v1/inference` is gone. No provider SDKs or provider keys belong in this repo.
  - The embedding model name plus its dimensions (`model_gateway.embed_model` / `embed_dimensions`) are the **index's identity**. `embed()` rejects any response from another name or dimension, and ingestion stores `embedding_model` in each chunk's metadata. Changing the model means a re-index.
  - `embed()` batches by `embed_batch_size` (gateway max 2,048 texts/call), forwards `X-Request-ID`, and raises `ModelGatewayError`. Retrieval maps that to `DependencyUnavailableError` (503).
  - `probe()` never raises. It checks `/v1/capabilities` (model granted, available, dimensions match) and then the gateway's `/v1/ready` for that model's provider specifically. The gateway's own readiness is green while *any* provider answers. Our `/v1/ready` gates on this and nothing else, and `/v1/health` depends on nothing.
- **Empty results are honest.** An empty index or empty embedding returns `citations: []`, never an error or a fabricated hit.
- **Auth** (`security/api_keys.py`, `api/deps.py`): `X-API-Key` is matched by salted sha256 against records from `AIR_RAG__SECURITY__API_KEYS_INLINE` (JSON array) or `API_KEYS_FILE`. Raw keys never live in config. Scopes come from `constants.py` (`retrieval:read`, `admin:read`) and are enforced with `require_scope(...)`. `allow_unauthenticated` is rejected when `env=production`.
- **Errors** (`api/errors.py`): everything leaves as RFC 9457 `application/problem+json`. To add an error, add an `AppError` subclass with `slug`/`title`/`status` and register it in `_BY_STATUS`. Raise those subclasses rather than `HTTPException`.
- **Middleware** (`api/middleware.py`) is raw ASGI, deliberately not `BaseHTTPMiddleware`, so structlog contextvars bound downstream stay visible for cleanup. It handles request-id (`X-Request-ID`), access log + Prometheus metrics, and the body-size limit.
- **Config** (`config.py`): pydantic-settings with prefix `AIR_RAG__` and `__` as the nesting delimiter (e.g. `AIR_RAG__MODEL_GATEWAY__BASE_URL`). Sections are `app`, `model_gateway`, `retrieval`, `security`, `obs`. `.env.example` documents every key. Keep it in sync when adding settings.
- `constants.py` values (headers, scopes, problem-type base URL, `DownstreamService`) show up in responses and metric labels, so renaming one is a breaking change.

## Testing conventions

- Every test builds the app via `create_app(settings)` with the explicit `settings` fixture in `tests/conftest.py`. Nothing reads the real environment.
- The gateway is mocked with `respx` at `GATEWAY_BASE_URL = http://air-model-gateway.invalid:8083`. The host is intentionally unresolvable, so an unmocked call fails loudly. Build gateway bodies with `embed_response()` and 768-d vectors with `vector(...)`. Use the `reachable_gateway` / `unreachable_gateway` fixtures for readiness, and the `DEV_KEY` / `NO_SCOPE_KEY` + `auth()` helpers for auth.

## Style

Ruff (line length 100, rule sets include `S`, `T20`, so no `print`) and mypy `--strict` with the pydantic plugin. Modules use `from __future__ import annotations` and `Final` constants.
