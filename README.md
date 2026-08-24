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
air-platform  ──POST /v1/retrieve──▶  air-rag  ──embed──▶  air-llm
air-tools     ──POST /v1/retrieve──▶     │
                                          └──search──▶  vector store
                                                          (in-memory, Phase 0)
```

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

**air-llm is required.** Without it `/v1/ready` is a 503 naming it as the
cause — no query can be embedded without a model. The service still boots
and serves `/v1/health` regardless.

```bash
cd ../air-llm && make up
```

`make check` runs ruff, mypy (strict) and the tests.

## The vector store

Phase 0 ships `retrieval/memory.py`: brute-force cosine similarity, no
dependency beyond the standard library. Correct for a laptop and for tests,
gone on restart — the same trade-off `air-platform`'s in-memory session
store makes. A real backend implements `retrieval/base.py`'s `VectorStore`
Protocol; `main.py`'s construction is the only place that changes.
