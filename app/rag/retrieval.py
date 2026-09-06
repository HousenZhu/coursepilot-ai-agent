import re
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.db import SessionFactory
from app.observability.metrics import RETRIEVAL_RESULTS
from app.rag.embeddings import embed_query


async def search_course_materials(
    user_id: str,
    query: str,
    course_id: str,
    top_k: int,
) -> list[dict[str, Any]]:
    vector = await embed_query(query)
    vector_literal = "[" + ",".join(f"{value:.8f}" for value in vector) + "]"
    statement = text(
        """
        SELECT dc.content_id, dc.title, dc.page, dc.chunk_text,
               1 - (dc.embedding <=> CAST(:embedding AS vector)) AS score
        FROM agent.document_chunks dc
        JOIN enrollments e
          ON e."courseId" = dc.course_id AND e."studentId" = :user_id
        WHERE dc.course_id = :course_id
        ORDER BY dc.embedding <=> CAST(:embedding AS vector)
        LIMIT :top_k
        """
    )
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                statement,
                {
                    "embedding": vector_literal,
                    "user_id": user_id,
                    "course_id": course_id,
                    "top_k": top_k,
                },
            )
        ).mappings().all()
    query_terms = set(re.findall(r"[a-z0-9]+", query.casefold()))
    results = [
        {
            "content_id": row["content_id"],
            "title": row["title"],
            "page": row["page"],
            "excerpt": row["chunk_text"][:500],
            "score": round(float(row["score"]), 4),
            "lexical_overlap": len(
                query_terms
                & set(re.findall(r"[a-z0-9]+", str(row["chunk_text"]).casefold()))
            ),
        }
        for row in rows
    ]
    filtered = [
        result for result in results if result["score"] >= get_settings().retrieval_min_score
    ]
    filtered.sort(
        key=lambda item: (item["lexical_overlap"], item["score"]), reverse=True
    )
    for result in filtered:
        result.pop("lexical_overlap", None)
    RETRIEVAL_RESULTS.observe(len(filtered))
    return filtered
