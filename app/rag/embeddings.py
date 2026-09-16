import asyncio
import time
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import get_settings
from app.observability.metrics import STAGE_LATENCY


@lru_cache
def _model() -> SentenceTransformer:
    return SentenceTransformer(get_settings().embedding_model, revision=get_settings().embedding_revision)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    started = time.perf_counter()
    try:
        vectors = await asyncio.to_thread(
            lambda: _model().encode(texts, normalize_embeddings=True, show_progress_bar=False)
        )
    finally:
        STAGE_LATENCY.labels(stage="embedding").observe(time.perf_counter() - started)
    return [vector.tolist() for vector in vectors]


async def embed_query(query: str) -> list[float]:
    return (await embed_texts([query]))[0]
