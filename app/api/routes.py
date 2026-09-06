import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.db import SessionFactory
from app.rag.ingestion import index_course
from app.repositories.agent import AgentRepository
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

    async def events() -> AsyncIterator[bytes]:
        service = AgentService(checkpointer)
        async for event, data in service.stream_run(
            user_id=auth.user_id,
            conversation_id=payload.conversation_id,
            message=payload.message.strip(),
            course_id=payload.course_id,
        ):
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
        },
    )


@router.get("/v1/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(conversation_id: UUID, auth: CurrentAuth) -> ConversationResponse:
    try:
        messages = await AgentRepository().get_messages(auth.user_id, conversation_id)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
    return ConversationResponse(
        conversation_id=conversation_id,
        messages=[
            ConversationMessage(role=message.role, content=message.content, created_at=message.created_at)
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
