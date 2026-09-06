from pathlib import Path

import pytest

from app.rag.ingestion import _chunk_words, _read_local_content


def test_chunking_is_bounded_and_overlapping() -> None:
    words = [f"word-{index}" for index in range(30)]
    chunks = _chunk_words(" ".join(words), size=10, overlap=2)
    assert len(chunks) == 4
    assert chunks[0].split()[-2:] == chunks[1].split()[:2]
    assert all(len(chunk.split()) <= 10 for chunk in chunks)


def test_chunking_ignores_empty_text() -> None:
    assert _chunk_words("") == []


def test_read_local_content(tmp_path: Path) -> None:
    document = tmp_path / "course.pdf"
    document.write_bytes(b"course material")

    assert _read_local_content("course.pdf", str(tmp_path), 1024) == b"course material"


def test_read_local_content_rejects_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "private.pdf"
    outside.write_bytes(b"private")

    with pytest.raises(ValueError, match="escapes"):
        _read_local_content("../private.pdf", str(tmp_path), 1024)
