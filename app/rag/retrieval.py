from typing import Any
import time
from collections.abc import Sequence
from sqlalchemy.engine import RowMapping

from sqlalchemy import text

from app.config import get_settings
from app.db import SessionFactory
from app.observability.metrics import RETRIEVAL_RESULTS, STAGE_LATENCY
from app.rag.embeddings import embed_query


def reciprocal_rank_fusion(rankings: list[list[str]], limit: int = 6) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, source in enumerate(dict.fromkeys(ranking), 1):
            scores[source] = scores.get(source, 0) + 1 / (60 + rank)
    return sorted(scores, key=lambda key: (-scores[key], key))[:limit]


async def search_course_materials(user_id: str, query: str, course_id: str, top_k: int) -> list[dict[str, Any]]:
    settings = get_settings()
    vector = await embed_query(query)
    started = time.perf_counter()
    scope = """
        FROM agent.document_chunks dc
        JOIN agent.document_versions dv ON dv.content_id = dc.content_id
          AND dv.active_version = dc.chunk_metadata->>'document_version'
        WHERE dc.course_id = :course_id
          AND EXISTS (SELECT 1 FROM enrollments e
                      WHERE e."courseId" = dc.course_id AND e."studentId" = :user_id)
    """
    columns = """SELECT dc.id::text AS source_id, dc.content_id, dc.title, dc.page,
                 dc.chunk_text AS excerpt, dv.active_version AS document_version"""
    params = {"user_id": user_id, "course_id": course_id, "query": query,
              "embedding": "[" + ",".join(str(value) for value in vector) + "]",
              "min_score": settings.retrieval_min_score}
    async with SessionFactory() as session:
        vector_rows = (await session.execute(text(
            columns + scope + """
            AND 1 - (dc.embedding <=> CAST(:embedding AS vector)) >= :min_score
            ORDER BY dc.embedding <=> CAST(:embedding AS vector), dc.id LIMIT 20
            """), params)).mappings().all()
        lexical_rows: Sequence[RowMapping] = []
        if settings.retrieval_mode == "hybrid":
            lexical_rows = (await session.execute(text(
                columns + scope + """
                AND to_tsvector('english', dc.chunk_text) @@ plainto_tsquery('english', :query)
                ORDER BY ts_rank_cd(to_tsvector('english', dc.chunk_text),
                                    plainto_tsquery('english', :query)) DESC, dc.id LIMIT 20
                """), params)).mappings().all()
    records = {row["source_id"]: dict(row) for row in [*vector_rows, *lexical_rows]}
    rankings = [[row["source_id"] for row in rows] for rows in [vector_rows, lexical_rows]]
    ids = reciprocal_rank_fusion(rankings, min(max(top_k, 1), 6)) if settings.retrieval_mode == "hybrid" else rankings[0][:min(top_k, 6)]
    results = [{**records[key], "excerpt": records[key]["excerpt"][:500]} for key in ids]
    RETRIEVAL_RESULTS.observe(len(results))
    STAGE_LATENCY.labels(stage="retrieval_sql_and_fusion").observe(time.perf_counter() - started)
    return results


async def valid_sources(user_id: str, citations: list[dict[str, Any]]) -> bool:
    if not citations:
        return False
    async with SessionFactory() as session:
        for citation in citations:
            row = (await session.execute(text("""
                SELECT dc.chunk_text FROM agent.document_chunks dc
                JOIN agent.document_versions dv ON dv.content_id = dc.content_id
                WHERE dc.id::text = :source_id AND dv.active_version = :version
                  AND dc.chunk_metadata->>'document_version' = dv.active_version
                  AND dc.content_id = :content_id AND dc.page = :page
                  AND EXISTS (SELECT 1 FROM enrollments e
                              WHERE e."courseId" = dc.course_id AND e."studentId" = :user_id)
            """), {"source_id": citation.get("source_id"), "version": citation.get("document_version"),
                   "content_id": citation["content_id"], "page": citation["page"], "user_id": user_id})).first()
            if row is None or not citation["excerpt"] or citation["excerpt"] not in row[0]:
                return False
    return True
