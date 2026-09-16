import asyncio
import os

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionFactory
from app.models import DocumentChunk
from app.rag import ingestion
from evals.pdf_fixture import make_pdf

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_UPGRADE_INTEGRATION_TESTS") != "1", reason="Disposable upgrade database only")]


async def test_page_move_deletion_and_identical_text_reuse(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    path = tmp_path / "pages.pdf"
    path.write_bytes(make_pdf(["Keep this page.", "Remove this page.", "Move this page."]))

    async def contents(self, course_id):
        return [{"id": "page-publication-test", "title": "Publication", "file_url": "/pages.pdf"}]

    embedded = []

    async def embed(values):
        embedded.extend(values)
        return [[1.0] + [0.0] * 383 for _ in values]

    monkeypatch.setattr(ingestion.LMSRepository, "get_pdf_contents", contents)
    monkeypatch.setattr(ingestion, "embed_texts", embed)
    await ingestion.index_course("eval-course-web")
    path.write_bytes(make_pdf(["Move this page.", "Keep this page."]))
    result = await ingestion.index_course("eval-course-web")
    assert result["embedded_chunks"] == 0 and result["reused_chunks"] == 2
    assert len(embedded) == 3
    async with SessionFactory() as session:
        chunks = (await session.scalars(select(DocumentChunk).where(
            DocumentChunk.content_id == "page-publication-test").order_by(DocumentChunk.page))).all()
    assert [(chunk.page, chunk.chunk_text) for chunk in chunks] == [
        (1, "Move this page."), (2, "Keep this page.")]


async def test_simultaneous_index_requests_publish_once(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    (tmp_path / "concurrent.pdf").write_bytes(make_pdf(["One immutable version."]))

    async def contents(self, course_id):
        return [{"id": "concurrent-publication-test", "title": "Concurrency", "file_url": "/concurrent.pdf"}]

    calls = []

    async def embed(values):
        calls.append(values)
        await asyncio.sleep(0.05)
        return [[1.0] + [0.0] * 383 for _ in values]

    monkeypatch.setattr(ingestion.LMSRepository, "get_pdf_contents", contents)
    monkeypatch.setattr(ingestion, "embed_texts", embed)
    results = await asyncio.gather(ingestion.index_course("eval-course-web"), ingestion.index_course("eval-course-web"))
    assert sum(result["documents"] for result in results) == 1
    assert sum(result["embedded_chunks"] for result in results) == 1
    assert len(calls) == 1
    async with SessionFactory() as session:
        chunks = (await session.scalars(select(DocumentChunk).where(
            DocumentChunk.content_id == "concurrent-publication-test"))).all()
    assert len(chunks) == 1
