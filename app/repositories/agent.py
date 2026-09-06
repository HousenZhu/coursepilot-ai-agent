from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.config import get_settings
from app.db import SessionFactory
from app.models import AgentRun, Conversation, Message, StudyPlanRecord


class AgentRepository:
    async def get_or_create_conversation(
        self, user_id: str, conversation_id: UUID | None
    ) -> Conversation:
        async with SessionFactory() as session:
            if conversation_id is not None:
                conversation = await session.scalar(
                    select(Conversation).where(
                        Conversation.id == conversation_id,
                        Conversation.user_id == user_id,
                    )
                )
                if conversation is None:
                    raise PermissionError("Conversation does not exist or is not owned by this user")
                return conversation

            conversation = Conversation(user_id=user_id)
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
            return conversation

    async def add_message(self, conversation_id: UUID, role: str, content: str) -> None:
        async with SessionFactory() as session:
            session.add(Message(conversation_id=conversation_id, role=role, content=content))
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(updated_at=datetime.now(UTC))
            )
            await session.commit()

    async def get_messages(self, user_id: str, conversation_id: UUID) -> list[Message]:
        async with SessionFactory() as session:
            owner = await session.scalar(
                select(Conversation.id).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )
            if owner is None:
                raise PermissionError("Conversation does not exist or is not owned by this user")
            result = await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.asc())
                .limit(100)
            )
            return list(result)

    async def start_run(self, conversation_id: UUID, user_id: str, trace_id: str) -> UUID:
        run = AgentRun(
            conversation_id=conversation_id,
            user_id=user_id,
            trace_id=trace_id,
            status="running",
            model=get_settings().llm_model,
        )
        async with SessionFactory() as session:
            session.add(run)
            await session.commit()
            return run.id

    async def finish_run(
        self,
        run_id: UUID,
        *,
        status: str,
        latency_ms: int,
        tool_calls: list[dict[str, Any]],
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        error_code: str | None = None,
    ) -> None:
        async with SessionFactory() as session:
            await session.execute(
                update(AgentRun)
                .where(AgentRun.id == run_id)
                .values(
                    status=status,
                    latency_ms=latency_ms,
                    tool_calls=tool_calls,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    error_code=error_code,
                )
            )
            await session.commit()

    async def save_study_plan(
        self,
        user_id: str,
        course_id: str | None,
        horizon_days: int,
        plan: dict[str, Any],
    ) -> StudyPlanRecord:
        async with SessionFactory() as session:
            await session.execute(
                update(StudyPlanRecord)
                .where(
                    StudyPlanRecord.user_id == user_id,
                    StudyPlanRecord.course_id == course_id,
                    StudyPlanRecord.status == "active",
                )
                .values(status="superseded")
            )
            record = StudyPlanRecord(
                id=uuid4(),
                user_id=user_id,
                course_id=course_id,
                horizon_days=horizon_days,
                plan=plan,
                status="active",
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def get_active_study_plan(
        self, user_id: str, course_id: str | None
    ) -> StudyPlanRecord | None:
        conditions = [
            StudyPlanRecord.user_id == user_id,
            StudyPlanRecord.status == "active",
        ]
        if course_id is not None:
            conditions.append(StudyPlanRecord.course_id == course_id)
        async with SessionFactory() as session:
            return await session.scalar(
                select(StudyPlanRecord)
                .where(*conditions)
                .order_by(StudyPlanRecord.created_at.desc())
                .limit(1)
            )
