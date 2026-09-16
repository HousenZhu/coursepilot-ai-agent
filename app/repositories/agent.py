from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import get_settings
from app.db import SessionFactory
from app.models import AgentRun, Conversation, Message, StudyPlanRecord
from app.db import engine


class RunConflict(Exception):
    pass


class RunLease:
    def __init__(self, run: AgentRun, connection: AsyncConnection | None = None) -> None:
        self.run = run
        self.connection = connection

    async def close(self) -> None:
        if self.connection is not None:
            connection, self.connection = self.connection, None
            try:
                await connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                                         {"key": f"conversation:{self.run.conversation_id}"})
            except BaseException:
                await connection.invalidate()
                raise
            finally:
                await connection.close()


class AgentRepository:
    def __init__(self, *, stage_plans: bool = False) -> None:
        self.stage_plans = stage_plans
        self.pending_plans: list[StudyPlanRecord] = []

    async def reserve_run(self, user_id: str, conversation_id: UUID | None, message: str,
                          course_id: str | None, key: str | None) -> RunLease:
        import hashlib
        import json

        request_hash = hashlib.sha256(json.dumps(
            [str(conversation_id) if conversation_id else None, message, course_id],
            separators=(",", ":"), ensure_ascii=False,
        ).encode()).hexdigest()
        connection = None
        try:
            async with SessionFactory() as session, session.begin():
                if key:
                    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                                          {"key": f"request:{user_id}:{key}"})
                    existing = await session.scalar(select(AgentRun).where(
                        AgentRun.user_id == user_id, AgentRun.idempotency_key == key))
                    if existing:
                        if existing.request_hash != request_hash:
                            raise RunConflict("Idempotency key belongs to a different request")
                        if existing.status != "completed":
                            raise RunConflict(f"Request is {existing.status}; it will not be replayed")
                        return RunLease(existing)
                if conversation_id:
                    conversation = await session.scalar(select(Conversation).where(
                        Conversation.id == conversation_id, Conversation.user_id == user_id))
                    if conversation is None:
                        raise PermissionError("Conversation not found")
                else:
                    conversation = Conversation(id=uuid4(), user_id=user_id)
                    session.add(conversation)
                connection = await engine.connect()
                acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                                                   {"key": f"conversation:{conversation.id}"})
                if not acquired:
                    raise RunConflict("Conversation already has an active request")
                # Possession of the lock proves any older running row has lost its worker.
                await session.execute(update(AgentRun).where(
                    AgentRun.conversation_id == conversation.id, AgentRun.status == "running"
                ).values(status="failed", error_code="worker_lost"))
                run = AgentRun(id=uuid4(), conversation_id=conversation.id, user_id=user_id,
                               trace_id=uuid4().hex, status="running", model=get_settings().llm_model,
                               idempotency_key=key, request_hash=request_hash)
                session.add(run)
                session.add(Message(conversation_id=conversation.id, role="user", content=message))
            return RunLease(run, connection)
        except BaseException:
            if connection is not None:
                await connection.invalidate()
                await connection.close()
            raise

    async def get_run(self, user_id: str, run_id: UUID) -> AgentRun | None:
        async with SessionFactory() as session:
            run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id))
            if run is not None and run.status == "running":
                async with engine.connect() as connection:
                    key = {"key": f"conversation:{run.conversation_id}"}
                    acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"), key)
                    if acquired:
                        try:
                            await session.execute(update(AgentRun).where(AgentRun.id == run.id, AgentRun.status == "running")
                                                  .values(status="failed", error_code="worker_lost"))
                            await session.commit()
                            await session.refresh(run)
                        finally:
                            try:
                                await connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), key)
                            except BaseException:
                                # A session-level lock must never return to the pool held.
                                await connection.invalidate()
                                raise
            return run

    async def complete_run(self, run_id: UUID, final: dict[str, Any], latency_ms: int,
                           tool_calls: list[dict[str, Any]], usage: dict[str, int]) -> None:
        async with SessionFactory() as session, session.begin():
            run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
            if run is None or run.status != "running":
                raise RunConflict("Run is no longer active")
            for plan in self.pending_plans:
                await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                                      {"key": f"plan:{run.user_id}:{plan.course_id}"})
                await session.execute(update(StudyPlanRecord).where(
                    StudyPlanRecord.user_id == run.user_id, StudyPlanRecord.course_id == plan.course_id,
                    StudyPlanRecord.status == "active").values(status="superseded"))
                session.add(plan)
            session.add(Message(conversation_id=run.conversation_id, role="assistant", content=final["answer_markdown"], response_payload=final))
            run.status, run.final_response, run.latency_ms = "completed", final, latency_ms
            run.tool_calls = tool_calls
            run.prompt_tokens = usage.get("input_tokens")
            run.completion_tokens = usage.get("output_tokens")
        self.pending_plans.clear()

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
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(100)
            )
            return list(reversed(list(result)))

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
                .where(AgentRun.id == run_id, AgentRun.status == "running")
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
        if self.stage_plans:
            if self.pending_plans:
                return self.pending_plans[0]
            record = StudyPlanRecord(id=uuid4(), user_id=user_id, course_id=course_id,
                                     horizon_days=horizon_days, plan=plan, status="active", created_at=datetime.now(UTC))
            self.pending_plans.append(record)
            return record
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
