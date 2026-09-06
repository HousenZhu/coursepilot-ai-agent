import asyncio
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import get_settings


@lru_cache
def _model() -> SentenceTransformer:
    return SentenceTransformer(get_settings().embedding_model)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    vectors = await asyncio.to_thread(
        _model().encode,
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [vector.tolist() for vector in vectors]


async def embed_query(query: str) -> list[float]:
    return (await embed_texts([query]))[0]
