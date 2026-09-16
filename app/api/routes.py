import json
import asyncio
import hashlib
from contextlib import aclosing
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.db import SessionFactory
from app.rag.ingestion import index_course
from app.repositories.agent import AgentRepository, RunConflict
from app.config import get_settings
from app.rag.ingestion import _read_content
from app.repositories.lms import LMSRepository
from app.schemas import (
    AgentRunRequest,
    ConversationMessage,
    ConversationResponse,
    HealthResponse,
)
from app.security import CurrentAuth
from app.services.agent_service import AgentService


router = APIRouter()


def _sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode()


@router.post("/v1/agent/runs/stream")
async def stream_agent_run(
    payload: AgentRunRequest,
    auth: CurrentAuth,
    request: Request,
) -> StreamingResponse:
    checkpointer = request.app.state.checkpointer
    if auth.role != "STUDENT":
        raise HTTPException(403, "Student role required")
    key = request.headers.get("Idempotency-Key")
    if key is not None and (not key.strip() or len(key) > 128):
        raise HTTPException(400, "Invalid idempotency key")
    if not payload.message.strip():
        raise HTTPException(422, "Message must not be blank")
    if payload.course_id and not await LMSRepository().student_has_course(auth.user_id, payload.course_id):
        raise HTTPException(403, "Course is not accessible")
    try:
        async with asyncio.timeout(get_settings().run_timeout_seconds):
            lease = await AgentRepository().reserve_run(auth.user_id, payload.conversation_id,
                payload.message.strip(), payload.course_id, key)
    except RunConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(404, "Not found") from exc

    async def events() -> AsyncIterator[bytes]:
        service = AgentService(checkpointer)
        async with aclosing(service.stream_run(
            user_id=auth.user_id,
            conversation_id=payload.conversation_id,
            message=payload.message.strip(),
            course_id=payload.course_id,
            lease=lease,
        )) as stream:
            async for event, data in stream:
                if await request.is_disconnected():
                    break
                yield _sse(event, data)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Run-ID": str(lease.run.id),
        },
    )


@router.get("/v1/agent/runs/{run_id}")
async def get_run(run_id: UUID, auth: CurrentAuth) -> dict[str, Any]:
    run = await AgentRepository().get_run(auth.user_id, run_id)
    if run is None:
        raise HTTPException(404, "Not found")
    return {"run_id": str(run.id), "status": run.status, "final": run.final_response,
            "error_code": run.error_code, "trace_id": run.trace_id}


@router.get("/v1/sources/{source_id}")
async def get_source(source_id: UUID, auth: CurrentAuth) -> Response:
    async with SessionFactory() as session:
        row = (await session.execute(text("""
            SELECT dc.chunk_metadata->>'source_url' AS url, dv.file_hash
            FROM agent.document_chunks dc JOIN agent.document_versions dv ON dv.content_id = dc.content_id
            WHERE dc.id = :id AND dc.chunk_metadata->>'document_version' = dv.active_version
            AND EXISTS (SELECT 1 FROM enrollments e WHERE e."courseId" = dc.course_id AND e."studentId" = :user)
        """), {"id": source_id, "user": auth.user_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Source no longer available")
    data = await _read_content(row["url"])
    if hashlib.sha256(data).hexdigest() != row["file_hash"]:
        raise HTTPException(409, "Document changed; re-index before viewing this citation")
    return Response(data, media_type="application/pdf", headers={"Cache-Control": "private, no-store"})


@router.get("/v1/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(conversation_id: UUID, auth: CurrentAuth) -> ConversationResponse:
    try:
        messages = await AgentRepository().get_messages(auth.user_id, conversation_id)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
    return ConversationResponse(
        conversation_id=conversation_id,
        messages=[
            ConversationMessage(role=message.role, content=message.content, created_at=message.created_at,
                                citations=(message.response_payload or {}).get("citations", []),
                                studyPlan=(message.response_payload or {}).get("study_plan"))
            for message in messages
        ],
    )


@router.post("/internal/index/courses/{course_id}")
async def index_course_materials(course_id: str, auth: CurrentAuth) -> dict[str, int]:
    if auth.role != "TEACHER" or not await LMSRepository().teacher_owns_course(
        auth.user_id, course_id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return await index_course(course_id)


@router.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    try:
        async with SessionFactory() as session:
            await session.execute(text("SELECT 1"))
        return HealthResponse(status="ok")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
