import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db import SessionFactory, engine
from app.models import AgentRun, DocumentChunk, DocumentVersion, Message, StudyPlanRecord
from app.repositories.agent import AgentRepository, RunConflict
from app.services.agent_service import AgentService
from app.routing import IntentRoute
from langchain_core.messages import AIMessage

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_UPGRADE_INTEGRATION_TESTS") != "1",
                                                    reason="Disposable upgrade database only")]
USER = "eval-student-alpha"
OTHER = "eval-student-omega"


async def test_request_identity_scope_conflict_and_atomic_completion() -> None:
    repository = AgentRepository(stage_plans=True)
    key = uuid4().hex
    lease = await repository.reserve_run(USER, None, "plan", None, key)
    try:
        with pytest.raises(RunConflict):
            await repository.reserve_run(USER, None, "different", None, key)
        with pytest.raises(RunConflict):
            await repository.reserve_run(USER, lease.run.conversation_id, "another", None, uuid4().hex)
        with pytest.raises(PermissionError):
            await repository.reserve_run(OTHER, lease.run.conversation_id, "read", None, None)
        record = await repository.save_study_plan(USER, None, 7, {"items": []})
        async with SessionFactory() as session:
            assert await session.get(StudyPlanRecord, record.id) is None
        final = {"answer_markdown": "Plan completed", "conversation_id": str(lease.run.conversation_id)}
        await repository.complete_run(lease.run.id, final, 1, [], {})
        replay = await repository.reserve_run(USER, None, "plan", None, key)
        assert replay.run.id == lease.run.id and replay.run.final_response == final
        assert await repository.get_run(OTHER, lease.run.id) is None
        async with SessionFactory() as session:
            assert await session.get(StudyPlanRecord, record.id) is not None
            assert await session.scalar(select(func.count()).select_from(Message).where(
                Message.conversation_id == lease.run.conversation_id, Message.role == "assistant")) == 1
        other = await repository.reserve_run(OTHER, None, "plan", None, key)
        await other.close()
        assert other.run.id != lease.run.id
    finally:
        await lease.close()


async def test_runtime_role_cannot_write_lms_or_create_tables() -> None:
    async with engine.begin() as admin:
        await admin.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email text"))
    url = get_settings().database_url.replace("coursepilot:coursepilot@", "coursepilot_agent:test-runtime-password@")
    runtime = create_async_engine(url)
    try:
        async with runtime.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM users")) >= 2
            assert await connection.scalar(text("SELECT count(*) FROM agent.agent_runs")) >= 0
        for statement in ("UPDATE users SET name='forbidden'", "CREATE TABLE public.forbidden(id int)",
                          "SELECT email FROM users", "CREATE TABLE agent.forbidden(id int)"):
            with pytest.raises(Exception, match="permission denied"):
                async with runtime.begin() as connection:
                    await connection.execute(text(statement))
    finally:
        await runtime.dispose()


async def test_status_read_recovers_abandoned_run_but_not_active_worker() -> None:
    repo = AgentRepository()
    lease = await repo.reserve_run(USER, None, "interrupted", None, uuid4().hex)
    try:
        assert (await repo.get_run(USER, lease.run.id)).status == "running"
        assert await repo.get_run(OTHER, lease.run.id) is None
    finally:
        await lease.close()
    recovered = await repo.get_run(USER, lease.run.id)
    assert recovered.status == "failed" and recovered.error_code == "worker_lost"
    replacement = await repo.reserve_run(USER, lease.run.conversation_id, "new request", None, uuid4().hex)
    await replacement.close()


async def test_database_failure_rolls_back_plan_message_and_result() -> None:
    repo = AgentRepository(stage_plans=True)
    lease = await repo.reserve_run(USER, None, "atomic write", None, None)
    plan = await repo.save_study_plan(USER, None, 7, {"items": []})
    final = {"answer_markdown": "Atomic answer", "conversation_id": str(lease.run.conversation_id)}

    def fail_message_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO agent.messages"):
            raise RuntimeError("injected message write failure")

    try:
        event.listen(engine.sync_engine, "before_cursor_execute", fail_message_insert)
        try:
            with pytest.raises(RuntimeError, match="injected message write failure"):
                await repo.complete_run(lease.run.id, final, 1, [], {})
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", fail_message_insert)
        async with SessionFactory() as session:
            run = await session.get(AgentRun, lease.run.id)
            assert run.status == "running" and run.final_response is None
            assert await session.get(StudyPlanRecord, plan.id) is None
            assert await session.scalar(select(func.count()).select_from(Message).where(
                Message.conversation_id == lease.run.conversation_id, Message.role == "assistant")) == 0
        await repo.complete_run(lease.run.id, final, 1, [], {})
        messages = await repo.get_messages(USER, lease.run.conversation_id)
        assert messages[-1].response_payload == final
    finally:
        await lease.close()


async def test_source_view_owner_positive_and_cross_user_negative() -> None:
    from app.main import app
    from app.security import AuthContext, require_auth
    async with SessionFactory() as session:
        source = await session.scalar(select(DocumentChunk.id).where(DocumentChunk.content_id == "eval-content-handbook"))
    app.dependency_overrides[require_auth] = lambda: AuthContext(USER, "STUDENT")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            own = await client.get(f"/v1/sources/{source}")
            assert own.status_code == 200 and own.content.startswith(b"%PDF-")
            app.dependency_overrides[require_auth] = lambda: AuthContext(OTHER, "STUDENT")
            denied = await client.get(f"/v1/sources/{source}")
            assert denied.status_code == 404 and b"%PDF-" not in denied.content
    finally:
        app.dependency_overrides.clear()


async def test_cancellation_discards_staged_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AgentService(MemorySaver())
    lease = await service.agent_repository.reserve_run(USER, None, "draft", None, None)
    plan = await service.agent_repository.save_study_plan(USER, None, 7, {"items": []})
    class Graph:
        async def astream(self, *args: object, **kwargs: object):
            yield ("custom", {"event": "token", "data": {"delta": "Verified segment"}})
            await asyncio.sleep(100)
    monkeypatch.setattr("app.services.agent_service.build_agent_graph", lambda *args: Graph())
    stream = service.stream_run(user_id=USER, conversation_id=None, message="draft", course_id=None, lease=lease)
    assert (await anext(stream))[0] == "token"
    await stream.aclose()
    run = await service.agent_repository.get_run(USER, lease.run.id)
    assert run.status == "cancelled"
    async with SessionFactory() as session:
        assert await session.get(StudyPlanRecord, plan.id) is None


async def test_whole_request_timeout_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    class Graph:
        async def astream(self, *args: object, **kwargs: object):
            await asyncio.sleep(10)
            yield ("custom", {})
    monkeypatch.setattr("app.services.agent_service.build_agent_graph", lambda *args: Graph())
    monkeypatch.setattr(get_settings(), "run_timeout_seconds", 0.05)
    service = AgentService(MemorySaver())
    lease = await service.agent_repository.reserve_run(USER, None, "timeout", None, None)
    events = [event async for event in service.stream_run(user_id=USER, conversation_id=None,
              message="timeout", course_id=None, lease=lease)]
    assert events[-1][0] == "error"
    assert (await service.agent_repository.get_run(USER, lease.run.id)).status == "failed"


async def test_version_reuse_and_failed_publish_leave_old_index(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.rag import ingestion
    from evals.pdf_fixture import make_pdf
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    path = tmp_path / "fixture.pdf"
    path.write_bytes(make_pdf(["First page original.", "Second page unchanged."]))
    async def contents(self, course_id):
        return [{"id": "incremental-test", "title": "Fixture", "file_url": "/fixture.pdf"}]
    counts = []
    async def embed(values):
        counts.append(len(values))
        return [[1.0] + [0.0] * 383 for _ in values]
    monkeypatch.setattr(ingestion.LMSRepository, "get_pdf_contents", contents)
    monkeypatch.setattr(ingestion, "embed_texts", embed)
    result = await ingestion.index_course("eval-course-web")
    assert result["embedded_chunks"] == 2
    assert (await ingestion.index_course("eval-course-web"))["documents"] == 0
    path.write_bytes(make_pdf(["Second page unchanged.", "First page updated."]))
    result = await ingestion.index_course("eval-course-web")
    assert result["embedded_chunks"] == 1 and result["reused_chunks"] == 1
    async with SessionFactory() as session:
        before = (await session.get(DocumentVersion, "incremental-test")).active_version
    path.write_bytes(make_pdf(["Third revision must not publish."]))
    async def failure(values):
        raise TimeoutError("embedding dependency unavailable")
    monkeypatch.setattr(ingestion, "embed_texts", failure)
    with pytest.raises(TimeoutError):
        await ingestion.index_course("eval-course-web")
    async with SessionFactory() as session:
        assert (await session.get(DocumentVersion, "incremental-test")).active_version == before
        assert await session.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.content_id == "incremental-test")) == 2


async def test_api_idempotency_conflict_and_cross_user_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app
    from app.security import AuthContext, require_auth
    app.state.checkpointer = MemorySaver()
    app.dependency_overrides[require_auth] = lambda: AuthContext(USER, "STUDENT")
    repo = AgentRepository()
    lease = await repo.reserve_run(USER, None, "first", None, "api-" + uuid4().hex)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/agent/runs/stream", json={"message": "other", "conversation_id": str(lease.run.conversation_id)})
            assert response.status_code == 409
            app.dependency_overrides[require_auth] = lambda: AuthContext(OTHER, "STUDENT")
            assert (await client.get(f"/v1/agent/runs/{lease.run.id}")).status_code == 404
    finally:
        app.dependency_overrides.clear()
        await lease.close()


@pytest.mark.parametrize("capability", ["assessment_records", "deadlines", "student_profile", "study_plan_mutation", "course_materials", "invalid_source"])
async def test_real_graph_with_controlled_model(monkeypatch: pytest.MonkeyPatch, capability: str) -> None:
    class ControlledModel:
        structured = False
        def __init__(self, **kwargs):
            pass
        def with_structured_output(self, schema, **kwargs):
            router = ControlledModel()
            router.structured = True
            return router
        def bind_tools(self, tools):
            return self
        async def ainvoke(self, messages):
            if self.structured:
                return IntentRoute(mode="execute_then_answer" if capability == "study_plan_mutation" else "retrieve_then_answer",
                    capabilities=["course_materials" if capability == "invalid_source" else capability],
                    mutates_state=capability == "study_plan_mutation",
                    horizon_days=3,
                    course_id="eval-course-web", query="HTML structures documents", reason="Fixture route")
            from langchain_core.messages import ToolMessage
            observations = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage)]
            if not observations:
                name = "search_course_materials" if capability in {"course_materials", "invalid_source"} else "get_learning_snapshot"
                section = {"assessment_records": "assessments", "deadlines": "deadlines"}.get(capability, "profile")
                args = {"query": "HTML structures documents"} if name == "search_course_materials" else {"sections": [section]}
                if capability == "study_plan_mutation":
                    args = {"sections": ["profile", "assessments", "deadlines"]}
                return AIMessage(content="", tool_calls=[{"id": "read", "name": name, "args": args}])
            first = observations[0]
            if capability in {"course_materials", "invalid_source"}:
                source = first["citations"][0]
                key = "invented" if capability == "invalid_source" else source["source_id"]
                return AIMessage(content=json.dumps({"paragraphs": [{"text": "UNVERIFIED-DRAFT" if capability == "invalid_source" else source["excerpt"], "evidence_ids": [key]}]}))
            evidence = first["sections"][0]["evidence"][0]
            key = evidence["id"]
            if capability == "study_plan_mutation" and len(observations) == 1:
                return AIMessage(content="", tool_calls=[{"id": "stage", "name": "stage_study_plan", "args": {
                    "horizon_days": 3,
                    "items": [{"day_index": day, "title": "Practice HTML", "minutes": 30,
                               "priority": "high", "reason": "Build on your course progress", "evidence_ids": [key]}
                              for day in (1, 2, 3)]}}])
            return AIMessage(content=json.dumps({"paragraphs": [{"text": "{{" + key + "}}. Practice the weakest topic next.", "evidence_ids": [key]}]}))
    monkeypatch.setattr("app.agent.graph.chat_model", ControlledModel)
    service = AgentService(MemorySaver())
    events = [event async for event in service.stream_run(user_id=USER, conversation_id=None,
        message="Read verified records", course_id="eval-course-web")]
    assert events[-1][0] == "final", events
    final = events[-1][1]
    assert final["outcome"] == ("dependency_failure" if capability == "invalid_source" else "answer")
    assert "".join(data["delta"] for event, data in events if event == "token") == final["answer_markdown"]
    if capability == "invalid_source":
        assert "UNVERIFIED-DRAFT" not in str(events)
        assert not final["citations"]
    elif capability == "course_materials":
        assert final["citations"] and final["citations"][0]["source_id"] in final["answer_markdown"]
    elif capability == "study_plan_mutation":
        assert final["study_plan"]
    else:
        assert final["facts"]
