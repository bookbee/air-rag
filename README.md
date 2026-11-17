# air-rag-service

Document search for the AIR platform: given a question, it returns the passages of
the knowledge base that are most related to it, each with its source, so that an
answer can be grounded in real documents and cite them. This pattern is called
retrieval-augmented generation (RAG).

> **Part of the AIR platform.** New here? Start with the
> [platform overview](../air-web-console/docs/platform/01-overview.md), the
> [concepts page](../air-web-console/docs/platform/02-concepts.md) (embeddings,
> vector store, chunks, citations) and the
> [local setup guide](../air-web-console/docs/platform/03-local-setup.md) in
> air-web-console.

> **Status (2026-11-16): a working first phase, not yet production-shaped.**
> Real and tested: the `POST /v1/retrieve` contract, tenant isolation by API key,
> strict validation, a relevance floor, health/ready/capabilities, and a chunk →
> embed → index pipeline that seeds a 27-document sample corpus at start-up. Verified
> end to end with air-orchestrator-service (cited answers).
> **Not built:** a persistent vector store (the index lives in process memory and is
> rebuilt on every start), any route or job that ingests real documents, and
> reranking. [`docs/00-plan.md`](docs/00-plan.md) has the goals and the phased roadmap.

## Where it sits

air-orchestrator-service's `gather` stage calls `POST /v1/retrieve` for passages and
their sources (its spec 0011). That is the only caller today; anything else that
needs a document lookup would call the same endpoint the same way.

```text
air-orchestrator-service ──POST /v1/retrieve──▶ air-rag-service ──POST /v1/embed──▶ air-model-gateway
                                                     │                              (model `local-768-v1`)
                                                     └──search──▶ vector store
                                                                  (in process memory)
```

How a search works:

1. At start-up, each document in the seed directory is split into chunks of about
   800 characters, and each chunk is turned into a 768-number vector (an *embedding*)
   by air-model-gateway. The vectors are kept in memory.
2. A query is embedded the same way.
3. The chunks whose vectors are closest to the query's (cosine similarity) are
   returned as `citations`, filtered to the caller's tenant and to active documents,
   and dropped if they score below `RETRIEVAL__MIN_SCORE` (0.5).

**Tenant-scoped by the API key.** Every key record carries a `tenant`, and
`POST /v1/retrieve` searches only that tenant's documents. The tenant is
never read from the body: the request rejects unknown fields (422), so a
`tenant` sent in the body gets a 422 rather than being ignored. A key without a
tenant fails startup, and unauthenticated mode needs
`AIR_RAG_SERVICE__SECURITY__ANONYMOUS_TENANT`. Documents marked `status: archived`
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

This phase ships `retrieval/memory.py`: brute-force cosine similarity, no
dependency beyond the standard library. Correct for a laptop and for tests,
gone on restart: the same trade-off air-orchestrator-service's in-memory session
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
ingested **at startup** when `AIR_RAG_SERVICE__INGESTION__SEED_DIR` is set.
In docker that is the default; on the host, `make run` does not seed and
`make run-sit` does:

```bash
make up            # docker: seeded from the mounted sit/corpus by default
make run-sit       # host: :8087, seeded from sit/corpus (air-model-gateway must be up)
make sit-check     # sit/queries.json: expected top-3 hit per query, plus no
                   # cross-tenant, archived or untenanted citation; non-zero on failure
```

### Replacing the seed corpus (DevOps)

The SIT corpus is a **placeholder** default, there so a new developer's
`make up` or `docker compose up` never starts with an empty index. It is
dummy data and must not reach a real environment. As the project grows,
replace it with real seeds:

- **Where the default lives:** `docker-compose.yml` sets
  `AIR_RAG_SERVICE__INGESTION__SEED_DIR` to
  `${AIR_RAG_SERVICE_SEED_DIR-/app/sit/corpus}` and mounts `./sit/corpus`
  read-only at that path. Nothing else defaults it: the image and
  `config.py` leave seeding off, and production uses neither compose file.
- **To point at another corpus:** mount it into the container and set
  `AIR_RAG_SERVICE_SEED_DIR` to the in-container path (or change the default
  and the volume in `docker-compose.yml`). An empty value,
  `AIR_RAG_SERVICE_SEED_DIR= make up`, starts with no seeding.
- **What a seed file needs:** `*.md` / `*.txt` with front matter. `tenant` is
  required (a document without a valid one is skipped, logged as
  `seed.skipped`); `status` is `active` or `archived`.
- **Keep `make sit-check` honest:** `sit/queries.json` expects the SIT corpus,
  so a different default corpus needs its own query set.
- **Longer term:** startup seeding re-embeds everything on every boot into an
  in-memory index. Real document sources and a persistent vector store are
  on the roadmap in `docs/00-plan.md`; when they land, drop this default.

## Docker and the shared network

`make up` runs the container on the platform's shared `air-net` network
(created by `../air-infrastructure-service`'s `make up`), where sibling
containers reach it as **`http://air-rag-service:8080`**. From the host it's still
`localhost:8087`. air-model-gateway is never on air-net, so the container
reaches it at `host.docker.internal:8083`.

```bash
cd ../air-infrastructure-service && make up   # air-net (make up fails early without it)
cd ../air-model-gateway && make up            # embeddings
make up                                       # air-rag-service on air-net, seeded with the SIT corpus
AIR_RAG_SERVICE_SEED_DIR= make up             # ...with an empty index instead
make up INFRA=none                            # own network instead (docker-compose.standalone.yml)
make status | logs | down
```

If the gateway can't embed at boot, seeding stops with a `seed.failed` log
line and the service still starts. `/v1/capabilities`' `document_count`
shows how many chunks made it in.

## Deployment notes

```bash
export IMAGE_REPOSITORY=<region>-docker.pkg.dev/<project>/air/air-rag-service
make image-push                                   # linux/amd64, tagged with this commit
make deploy DEPLOY_ENV=uat KUBE_CONTEXT=<context> GCP_PROJECT=<project>
make deploy-smoke DEPLOY_ENV=uat KUBE_CONTEXT=<context>
```

This repository owns its `Dockerfile` and image. The Helm chart, the values for UAT
and production, the Terraform and the deploy scripts are in
[air-infrastructure-service](../air-infrastructure-service/gke/README.md) (`gke/`),
where DevOps keeps them for every core service; the targets above call into that
checkout (`INFRA_DIR`). Its [runbook](../air-infrastructure-service/docs/03-gke-runbook.md)
is the procedure, and `make deploy-render SAMPLE=1` prints what would be applied.
**If this service's start command, port or user changes, update its values files
there** (`gke/values/*/air-rag-service.yaml`).

There is no CI workflow and no deploy pipeline in this repository yet. The points that
matter most for a deployment:

- **The container listens on 8080** and runs as uid 10001. Liveness is
  `/v1/health`; readiness is `/v1/ready`, which is 503 until air-model-gateway can
  embed with `local-768-v1`.
- **The index is in memory.** Every replica builds its own copy at start-up from
  `AIR_RAG_SERVICE__INGESTION__SEED_DIR`, so start-up time grows with the corpus, and
  the gateway must be able to embed before the pod starts or the index is empty
  (`document_count` on `/v1/ready` shows it).
- **The gateway must serve the embedding model.** `local-768-v1` is a local model
  (`nomic-embed-text`), so the cluster needs a model server for it even when all chat
  traffic uses cloud models.
- **Every key in `.env.example` is a development value.** A deployment needs its own
  `HASH_SALT` and key records (one per tenant), and `APP__ENV=production`.

## More

- [`docs/00-plan.md`](docs/00-plan.md): goals, non-goals, decisions and the roadmap.
- [`docs/openapi.json`](docs/openapi.json): the API contract (`make openapi` regenerates it).
- [`CLAUDE.md`](CLAUDE.md): the compact engineering guide, also used by AI coding assistants.
- Bugs found in this service are tracked in
  [`air-web-console/bugs/rag/`](../air-web-console/bugs/rag/README.md).
