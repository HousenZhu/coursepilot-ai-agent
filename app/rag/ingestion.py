import asyncio
import hashlib
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader
from sqlalchemy import delete, select, text

from app.config import get_settings
from app.db import SessionFactory
from app.models import DocumentChunk, DocumentVersion
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
    return _extract_document(data, max_pages)[0]


def _extract_document(data: bytes, max_pages: int) -> tuple[list[dict[str, Any]], list[str]]:
    reader = PdfReader(BytesIO(data))
    if len(reader.pages) > max_pages:
        raise ValueError("PDF exceeds the configured page limit")
    chunks: list[dict[str, Any]] = []
    hashes: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        normalized = " ".join(unicodedata.normalize("NFKC", page.extract_text() or "").split())
        hashes.append(hashlib.sha256(normalized.encode()).hexdigest())
        for chunk in _chunk_words(normalized):
            chunks.append({"page": page_number, "text": chunk})
    return chunks, hashes


async def index_course(course_id: str) -> dict[str, int]:
    settings = get_settings()
    contents = await LMSRepository().get_pdf_contents(course_id)
    indexed_documents = 0
    indexed_chunks = 0
    embedded_chunks = 0
    reused_chunks = 0

    for content in contents:
        async with SessionFactory() as session, session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                                  {"key": f"document:{content['id']}"})
            data = await _read_content(str(content["file_url"]))
            digest = hashlib.sha256(data).hexdigest()
            pipeline = f"{settings.embedding_model}@{settings.embedding_revision}:{settings.chunker_version}"
            version = hashlib.sha256(f"{digest}:{pipeline}".encode()).hexdigest()
            current = await session.get(DocumentVersion, content["id"])
            if current and current.active_version == version:
                continue
            records, page_hashes = await asyncio.to_thread(_extract_document, data, settings.max_pdf_pages)
            previous = (await session.scalars(select(DocumentChunk).where(
                DocumentChunk.content_id == content["id"]))).all()
            cached = {
                row.chunk_metadata.get("chunk_hash"): list(row.embedding)
                for row in previous if row.chunk_metadata.get("pipeline_version") == pipeline
            }
            hashes = [hashlib.sha256(record["text"].encode()).hexdigest() for record in records]
            missing = {key: record["text"] for key, record in zip(hashes, records, strict=True) if key not in cached}
            vectors = await embed_texts(list(missing.values()))
            cached.update(dict(zip(missing, vectors, strict=True)))
            embedded_chunks += len(missing)
            reused_chunks += len(records) - len(missing)
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
                        embedding=cached[chunk_hash],
                        chunk_metadata={"source_url": content["file_url"], "chunk_hash": chunk_hash,
                                        "pipeline_version": pipeline, "document_version": version},
                    )
                    for record, chunk_hash in zip(records, hashes, strict=True)
                ]
            )
            if current is None:
                current = DocumentVersion(content_id=content["id"], course_id=course_id)
                session.add(current)
            current.file_hash, current.active_version = digest, version
            current.pipeline_version, current.page_hashes = pipeline, page_hashes
        indexed_documents += 1
        indexed_chunks += len(records)

    return {"documents": indexed_documents, "chunks": indexed_chunks,
            "embedded_chunks": embedded_chunks, "reused_chunks": reused_chunks}
