"""Seed the index from a folder of documents at startup.

The in-process index (Phase 0) can only be filled from inside the process,
and the public API is read-only (docs/00-plan.md G2), so a fixed corpus —
SIT's `sit/corpus/` — is ingested on boot when `ingestion.seed_dir` is set.

Each `*.md` / `*.txt` file is one document; its stem is the `source_id`. An
optional front-matter block supplies metadata, copied onto every chunk:

    ---
    title: Returns and refunds
    uri: https://example.test/help/returns
    kind: policy
    ---
    Body text…
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from air_rag.clients.model_gateway import ModelGatewayClient, ModelGatewayError
from air_rag.ingestion.base import Chunker, ingest_document
from air_rag.observability.logging import get_logger
from air_rag.retrieval.base import VectorStore

__all__ = ["SeedDocument", "load_seed_documents", "seed_store"]

_log = get_logger(__name__)

_SUFFIXES: Final[frozenset[str]] = frozenset({".md", ".txt"})
_FENCE: Final[str] = "---"


@dataclass(frozen=True, slots=True)
class SeedDocument:
    source_id: str
    text: str
    metadata: dict[str, str] = field(default_factory=dict)


def parse_document(source_id: str, raw: str) -> SeedDocument:
    lines = raw.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        return SeedDocument(source_id=source_id, text=raw)
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == _FENCE)
    except StopIteration:
        return SeedDocument(source_id=source_id, text=raw)
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep and key.strip():
            metadata[key.strip()] = value.strip()
    return SeedDocument(source_id=source_id, text="\n".join(lines[end + 1 :]), metadata=metadata)


def load_seed_documents(directory: Path) -> list[SeedDocument]:
    if not directory.is_dir():
        raise ValueError(f"ingestion.seed_dir is not a directory: {directory}")
    return [
        parse_document(path.stem, path.read_text(encoding="utf-8"))
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix in _SUFFIXES
    ]


async def seed_store(
    directory: Path,
    *,
    chunker: Chunker,
    model_gateway: ModelGatewayClient,
    store: VectorStore,
) -> int:
    """Ingest every document in `directory`; returns the chunks stored. A
    failure to embed is logged and stops seeding — the service still boots,
    and `/v1/capabilities`' `document_count` shows what made it in."""
    documents = load_seed_documents(directory)
    stored = 0
    for doc in documents:
        try:
            stored += await ingest_document(
                source_id=doc.source_id,
                text=doc.text,
                metadata=dict(doc.metadata),
                chunker=chunker,
                model_gateway=model_gateway,
                store=store,
            )
        except ModelGatewayError as exc:
            _log.error("seed.failed", source_id=doc.source_id, error=str(exc), chunks=stored)
            return stored
    _log.info("seed.complete", documents=len(documents), chunks=stored, dir=str(directory))
    return stored
