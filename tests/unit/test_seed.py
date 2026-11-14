"""Startup seeding: front matter, the folder loader, and the boot-time wiring."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from air_rag.api.deps import STATE_ATTR
from air_rag.clients.model_gateway import EmbeddingResult, ModelGatewayError
from air_rag.config import Settings
from air_rag.ingestion.base import FixedSizeChunker
from air_rag.ingestion.seed import load_seed_documents, parse_document, seed_store
from air_rag.main import create_app
from air_rag.retrieval.memory import InMemoryVectorStore
from tests.conftest import GATEWAY_BASE_URL, embed_response, vector

_SIT_CORPUS = Path(__file__).resolve().parents[2] / "sit" / "corpus"


class _FakeGateway:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self._fail_on = fail_on

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if self._fail_on and any(self._fail_on in t for t in texts):
            raise ModelGatewayError("boom")
        return EmbeddingResult(
            embeddings=[[1.0, 0.0] for _ in texts], embedding_model="local-768-v1", dimensions=2
        )


def test_front_matter_becomes_metadata_and_is_stripped_from_the_text() -> None:
    doc = parse_document("returns", "---\ntitle: Returns\nuri: https://x.test/r\n---\nBody.")

    assert doc.metadata == {"title": "Returns", "uri": "https://x.test/r"}
    assert doc.text == "Body."


def test_a_document_without_front_matter_is_all_text() -> None:
    assert parse_document("a", "just text").text == "just text"


def test_an_unclosed_front_matter_block_is_treated_as_text() -> None:
    doc = parse_document("a", "---\ntitle: x\nno closing fence")

    assert doc.metadata == {}


def test_loader_reads_md_and_txt_only(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text("bee")
    (tmp_path / "a.txt").write_text("ay")
    (tmp_path / "notes.json").write_text("{}")

    assert [d.source_id for d in load_seed_documents(tmp_path)] == ["a", "b"]


def test_loader_rejects_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a directory"):
        load_seed_documents(tmp_path / "nope")


async def test_seed_store_ingests_every_document_with_its_metadata(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text("---\ntitle: T\n---\n" + "x" * 250)
    (tmp_path / "empty.md").write_text("---\ntitle: E\n---\n")
    store = InMemoryVectorStore()

    stored = await seed_store(
        tmp_path,
        chunker=FixedSizeChunker(size=100),
        model_gateway=_FakeGateway(),  # type: ignore[arg-type]
        store=store,
    )

    assert stored == 3
    [hit] = await store.search([1.0, 0.0], top_k=1)
    assert hit.metadata["title"] == "T"
    assert hit.metadata["source_id"] == "doc"


async def test_seed_store_stops_on_a_gateway_failure_without_raising(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("fine")
    (tmp_path / "b.md").write_text("explode")
    (tmp_path / "c.md").write_text("never reached")
    store = InMemoryVectorStore()

    stored = await seed_store(
        tmp_path,
        chunker=FixedSizeChunker(),
        model_gateway=_FakeGateway(fail_on="explode"),  # type: ignore[arg-type]
        store=store,
    )

    assert stored == 1
    assert len(store) == 1


def test_the_sit_corpus_is_well_formed() -> None:
    docs = load_seed_documents(_SIT_CORPUS)

    assert len(docs) >= 25
    assert all(d.metadata.get("title") and d.metadata.get("uri") for d in docs)
    assert all(d.metadata["uri"].startswith("https://") for d in docs)
    assert any(not d.text.strip() for d in docs), "the empty-document edge case is missing"
    assert any(len(d.text) > 800 for d in docs), "the multi-chunk edge case is missing"


def test_every_sit_query_expects_a_document_that_exists() -> None:
    stems = {d.source_id for d in load_seed_documents(_SIT_CORPUS)}
    cases = json.loads((_SIT_CORPUS.parent / "queries.json").read_text())["cases"]

    assert {c["expect"] for c in cases} <= stems


@respx.mock
async def test_the_app_seeds_at_startup_when_seed_dir_is_set(
    settings: Settings, tmp_path: Path
) -> None:
    (tmp_path / "doc.md").write_text("---\ntitle: T\n---\nhello")

    def answer(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        return httpx.Response(200, json=embed_response([vector(1.0) for _ in texts]))

    respx.post(f"{GATEWAY_BASE_URL}/v1/embed").mock(side_effect=answer)
    seeded = settings.model_copy(
        update={"ingestion": settings.ingestion.model_copy(update={"seed_dir": tmp_path})}
    )
    app = create_app(seeded)

    async with app.router.lifespan_context(app):
        assert len(getattr(app.state, STATE_ATTR).store) == 1


def test_a_blank_seed_dir_means_no_seeding() -> None:
    settings = Settings.model_validate({"app": {"env": "test"}, "ingestion": {"seed_dir": ""}})

    assert settings.ingestion.seed_dir is None
