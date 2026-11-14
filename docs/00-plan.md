# air-rag — Plan

**Status:** Phase-0 skeleton · **Owner:** Vikas Roy
**Source:** Decided alongside `air-platform`'s open design questions
(`air-platform/docs/00-plan.md` §4), against `shopassist-service`
(`~/git/iisc-genai/shopassist-service`) and an enterprise reference diagram.

## 1. Problem statement

`air-platform` never retrieves or indexes anything itself (its own non-goal:
"No retrieval or indexing. Vector stores, chunking and embeddings are
air-rag's"). Something has to own the ingestion pipeline and the query-time
search that grounds an answer in real documents and gives it citations.

`shopassist-service`'s `RAGService` (`services/rag.py`,
`services/data_pipeline.py`) proved the shape working in one process — FAISS
over real embeddings, ingesting conversations, product descriptions and PDF
policy documents. air-rag is that shape, externalised as its own service with
its own API, called by both air-platform's GATHER step and any air-tools
agent that needs a lookup.

## 2. Goals

| # | Goal | Acceptance signal |
| --- | --- | --- |
| G1 | One retrieval contract, two kinds of caller | air-platform and air-tools both call `POST /v1/retrieve` identically |
| G2 | Read-only from the public API | No route writes to the index; ingestion is a separate internal path |
| G3 | All embeddings through air-model-gateway | No provider SDK, no provider key in this repo |
| G4 | A real backend is a drop-in | `retrieval/base.py`'s `VectorStore` Protocol is the only contract a real backend has to satisfy |
| G5 | Honest empty results | An unindexed query returns no citations, not a fabricated one |

## 3. Non-goals (v1)

- **No document parsing.** `FixedSizeChunker` is the naive baseline; PDF/HTML
  parsing is Phase 1, once a real document source exists to parse.
- **No mutation of any business system.** air-rag only ever reads and writes
  its own index.
- **No model hosting.** air-model-gateway (`POST /v1/embed`, `local-768-v1`) is the only embedding path.
- **No conversation/session state.** Each retrieval call is stateless.

## 4. Decisions taken

| Decision | Choice | Rationale |
| --- | --- | --- |
| Vector store | In-process (Phase 0), `VectorStore` Protocol (any backend, later) | Same trade-off air-platform's in-memory session store makes; correct for a laptop and tests |
| Caller shape | One endpoint, two callers (air-platform, air-tools' agents) | Confirmed with the user: agent reasoning lives in air-tools, and a RAG-backed agent calls air-rag the same way air-platform does |
| Tenancy | The tenant comes from the API key record (required); retrieval filters on it, and a body `tenant` is a 422; archived documents are never returned | Callers can't choose or spoof a tenant; fail closed, with no default tenant. Decided 2026-11-14 so air-orchestrator-service can enable air-rag on multi-tenant deployments |
| Auth | `X-API-Key`, salted-sha256, scopes — no channel concept | Same reasoning as air-tools: one caller shape, not air-platform's customer/business split |
| Language / runtime | Python 3.12, FastAPI, Pydantic v2 | Matches every other AIR service |
| Port | **8087** | air-infra/README.md's port map (bumped from 8083 once the model gateway claimed that slot) |

## 5. Phased delivery

### Phase 0 — Skeleton (this)

`pyproject.toml`, Makefile, Dockerfile, compose, config, app factory,
structured logging, RFC 9457 errors, API-key auth, `/v1/health` + `/v1/ready`
+ `/v1/capabilities`, `POST /v1/retrieve`, the in-memory `VectorStore`, and a
working chunk → embed → index pipeline exercised against synthetic text.

### Phase 1 — Real documents

A real chunker (document-structure-aware, not fixed-size), a real document
source (policy PDFs, product descriptions — `shopassist-service`'s `docs/` is
the model), and an ingestion route or job to drive it.

### Phase 2 — A real backend

Replace `InMemoryVectorStore` with a persisted backend behind the same
`VectorStore` Protocol, which must honour the `where` filter that tenant
isolation (decided in Phase 0) rests on.

Full HLD/LLD are deferred until Phase 1 has real documents to design against.
