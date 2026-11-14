# air-rag

Ingestion, indexing and retrieval for the AIR platform.

> **Status: Phase-0 skeleton.** Structure, config, auth, health/ready/capabilities,
> a real in-process vector store, and a working chunk → embed → index pipeline
> are real. No real document source is ingested yet — the pipeline has been
> exercised against synthetic text only. See [`docs/00-plan.md`](docs/00-plan.md).

## Where it sits

`air-platform`'s GATHER step calls `POST /v1/retrieve` for grounding and
citations (`air-platform/docs/01-hld.md` §4). Domain agents in `air-tools`
that need a RAG lookup (a product-recommendation agent, say) call the same
endpoint the same way — air-rag has one contract, two kinds of caller.

```text
air-platform  ──POST /v1/retrieve──▶  air-rag  ──embed──▶  air-model-gateway
air-tools     ──POST /v1/retrieve──▶     │
                                          └──search──▶  vector store
                                                          (in-memory, Phase 0)
```

**Tenant-scoped by the API key.** Every key record carries a `tenant`, and
`POST /v1/retrieve` searches only that tenant's documents. The tenant is
never read from the body: the request rejects unknown fields (422), so a
`tenant` sent in the body gets a 422 rather than being ignored. A key without a
tenant fails startup, and unauthenticated mode needs
`AIR_RAG__SECURITY__ANONYMOUS_TENANT`. Documents marked `status: archived`
stay indexed but are never returned.

**Read-only from every caller's perspective.** `POST /v1/retrieve` only
searches; nothing in the public API writes to the index. Ingestion
(`ingestion/base.py`) is a separate, internal pipeline — no route exposes it
yet, matching Phase 0's scope (structure and a real happy path, not every
operational surface).

## Quickstart

```bash
make dev            # .venv + toolchain
make env            # .env from .env.example, if absent
make run            # http://127.0.0.1:8087

curl -s localhost:8087/v1/health

curl -s -X POST localhost:8087/v1/retrieve \
  -H 'X-API-Key: airr_local_dev_key' -H 'Content-Type: application/json' \
  -d '{"query":"what is the return policy"}'
```

Against an empty index (nothing ingested yet) this returns an empty
`citations` list — the honest answer, not an error.

**air-model-gateway is required.** Every embedding goes through its
`POST /v1/embed` with the published model `local-768-v1` (768 dimensions), the
index's identity. `/v1/ready` is a 503 naming the cause unless the gateway
grants this service that model, reports those dimensions, and the provider
behind it is answering — no query can be embedded otherwise. The service still
boots and serves `/v1/health` regardless.

```bash
cd ../air-model-gateway && make up    # needs `ollama pull nomic-embed-text`
```

`make check` runs ruff, mypy (strict) and the tests.

## The vector store

Phase 0 ships `retrieval/memory.py`: brute-force cosine similarity, no
dependency beyond the standard library. Correct for a laptop and for tests,
gone on restart — the same trade-off `air-platform`'s in-memory session
store makes. A real backend implements `retrieval/base.py`'s `VectorStore`
Protocol; `main.py`'s construction is the only place that changes.

## SIT corpus

`sit/corpus/` is a dummy corpus for system integration testing: 27 documents
for a fictional store, Brightcart (tenant `brightcart`: 17 policies, 10
products), on reserved `.example` URLs. Front matter sets `tenant`
(required), `status` (`active` by default, or `archived`), and `title`, `uri`, `kind`,
which are copied into every chunk's `metadata`. Deliberate edge cases: an
archived near-duplicate of the returns policy (must never be returned), a
document for another tenant (`acme`), a draft with no tenant (skipped at
seeding), a warranty document long enough to span several chunks, an empty
document (stores nothing), and a product with a Devanagari name. The dev key
`airr_local_dev_key` belongs to tenant `brightcart`.

The index lives in memory and the public API is read-only, so the corpus is
ingested **at startup** when `AIR_RAG__INGESTION__SEED_DIR` is set:

```bash
make run-sit       # :8087, seeded from sit/corpus (air-model-gateway must be up)
make sit-check     # sit/queries.json: expected top-3 hit per query, plus no
                   # cross-tenant, archived or untenanted citation; non-zero on failure

AIR_RAG_SEED_DIR=/app/sit/corpus make up    # the same, in docker (corpus is mounted)
```

## Docker and the shared network

`make up` runs the container on the platform's shared `air-net` network
(created by `../air-infrastructure-service`'s `make up`), where sibling
containers reach it as **`http://air-rag:8080`**. From the host it's still
`localhost:8087`. air-model-gateway is never on air-net, so the container
reaches it at `host.docker.internal:8083`.

```bash
cd ../air-infrastructure-service && make up   # air-net (make up fails early without it)
cd ../air-model-gateway && make up            # embeddings
make up                                       # air-rag on air-net
AIR_RAG_SEED_DIR=/app/sit/corpus make up      # ...seeded with the SIT corpus
make up INFRA=none                            # own network instead (docker-compose.standalone.yml)
make status | logs | down
```

If the gateway can't embed at boot, seeding stops with a `seed.failed` log
line and the service still starts. `/v1/capabilities`' `document_count`
shows how many chunks made it in.
