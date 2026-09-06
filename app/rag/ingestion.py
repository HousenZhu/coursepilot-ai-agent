import asyncio
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader
from sqlalchemy import delete, select

from app.config import get_settings
from app.db import SessionFactory
from app.models import DocumentChunk
from app.rag.embeddings import embed_texts
from app.repositories.lms import LMSRepository


def _chunk_words(text: str, size: int = 400, overlap: int = 60) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    start = 0
    while start < len(words):
        chunk = " ".join(words[start : start + size]).strip()
        if chunk:
            chunks.append(chunk)
        if start + size >= len(words):
            break
        start += size - overlap
    return chunks


def _read_local_content(file_url: str, uploads_dir: str, max_pdf_bytes: int) -> bytes:
    root = Path(uploads_dir).resolve()
    candidate = (root / file_url.lstrip("/")).resolve()
    if root not in candidate.parents:
        raise ValueError("Content path escapes the configured upload directory")
    if candidate.stat().st_size > max_pdf_bytes:
        raise ValueError("PDF exceeds the configured size limit")
    return candidate.read_bytes()


async def _read_content(file_url: str) -> bytes:
    settings = get_settings()
    if file_url.startswith(("http://", "https://")):
        host = (urlparse(file_url).hostname or "").lower()
        allowed_hosts = [
            item.strip().lower()
            for item in settings.allowed_content_hosts.split(",")
            if item.strip()
        ]
        if not any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts):
            raise ValueError("Remote content host is not allowlisted")
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            async with client.stream("GET", file_url) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > settings.max_pdf_bytes:
                        raise ValueError("PDF exceeds the configured size limit")
                return bytes(data)

    return await asyncio.to_thread(
        _read_local_content,
        file_url,
        settings.uploads_dir,
        settings.max_pdf_bytes,
    )


def _extract_chunks(data: bytes, max_pages: int) -> list[dict[str, Any]]:
    reader = PdfReader(BytesIO(data))
    if len(reader.pages) > max_pages:
        raise ValueError("PDF exceeds the configured page limit")
    chunks: list[dict[str, Any]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        for chunk in _chunk_words(page.extract_text() or ""):
            chunks.append({"page": page_number, "text": chunk})
    return chunks


async def index_course(course_id: str) -> dict[str, int]:
    settings = get_settings()
    contents = await LMSRepository().get_pdf_contents(course_id)
    indexed_documents = 0
    indexed_chunks = 0

    for content in contents:
        data = await _read_content(str(content["file_url"]))
        digest = hashlib.sha256(data).hexdigest()
        async with SessionFactory() as session:
            existing = await session.scalar(
                select(DocumentChunk.id)
                .where(
                    DocumentChunk.content_id == content["id"],
                    DocumentChunk.content_hash == digest,
                )
                .limit(1)
            )
        if existing is not None:
            continue

        chunk_records = await asyncio.to_thread(_extract_chunks, data, settings.max_pdf_pages)

        embeddings = await embed_texts([record["text"] for record in chunk_records])
        async with SessionFactory() as session:
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.content_id == content["id"])
            )
            session.add_all(
                [
                    DocumentChunk(
                        course_id=course_id,
                        content_id=content["id"],
                        title=content["title"],
                        page=record["page"],
                        chunk_text=record["text"],
                        content_hash=digest,
                        embedding=embedding,
                        chunk_metadata={"source_url": content["file_url"]},
                    )
                    for record, embedding in zip(chunk_records, embeddings, strict=True)
                ]
            )
            await session.commit()
        indexed_documents += 1
        indexed_chunks += len(chunk_records)

    return {"documents": indexed_documents, "chunks": indexed_chunks}
