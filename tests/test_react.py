import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.react import ReactRuntime, qualitative_text
from app.tools import ToolContext


def runtime(model=None):
    lms = SimpleNamespace(
        student_has_course=AsyncMock(side_effect=lambda user, course: course == "mine"),
        get_student_profile=AsyncMock(return_value={"courses": [{"course_id": "mine", "title": "Web", "progress": 40}]}),
        get_assessment_performance=AsyncMock(return_value={"quiz_attempts": [], "average_quiz_score": 64}),
        get_upcoming_deadlines=AsyncMock(return_value=[]),
    )
    repo = SimpleNamespace(stage_plans=True, pending_plans=[], save_study_plan=AsyncMock())
    return ReactRuntime(ToolContext("student", "mine", lms, repo), model, 4)


def call(name, args, identifier="call-1"):
    return {"name": name, "args": args, "id": identifier, "type": "tool_call"}


@pytest.mark.parametrize("name,args", [
    ("get_learning_snapshot", {"sections": ["profile"], "course_id": "other"}),
    ("get_active_study_plan", {"course_id": "other"}),
    ("search_course_materials", {"query": "secret", "course_id": "other"}),
    ("get_learning_snapshot", {"sections": ["profile"], "user_id": "other"}),
    ("execute_sql", {"query": "select * from users"}),
    ("get_learning_snapshot", {"sections": ["profile"], "days": 99}),
])
async def test_invalid_calls_never_reach_repository(name, args):
    engine = runtime()
    engine.messages = [AIMessage(content="", tool_calls=[call(name, args)])]
    output = await engine.execute({})
    assert "error" in output["messages"][0].content
    engine.context.lms.get_student_profile.assert_not_called()
    engine.context.lms.get_assessment_performance.assert_not_called()


async def test_snapshot_queries_are_concurrent():
    engine = runtime()
    started = set()
    barrier = asyncio.Event()
    async def read(name, value):
        started.add(name)
        if len(started) == 3:
            barrier.set()
        await asyncio.wait_for(barrier.wait(), 1)
        return value
    engine.context.lms.get_student_profile.side_effect = lambda *a: None
    async def profile(*args):
        return await read("profile", {"courses": []})
    async def assessments(*args):
        return await read("assessments", {})
    async def deadlines(*args):
        return await read("deadlines", [])
    engine.context.lms.get_student_profile.side_effect = profile
    engine.context.lms.get_assessment_performance.side_effect = assessments
    engine.context.lms.get_upcoming_deadlines.side_effect = deadlines
    result = json.loads(await engine.by_name["get_learning_snapshot"].ainvoke(
        {"sections": ["profile", "assessments", "deadlines"]}))
    assert len(started) == 3
    assert all("error" not in section for section in result["sections"])


async def test_identical_parallel_reads_execute_once():
    engine = runtime()
    args = {"sections": ["profile"]}
    engine.messages = [AIMessage(content="", tool_calls=[call("get_learning_snapshot", args, str(i)) for i in range(2)])]
    output = await engine.execute({})
    assert len(output["messages"]) == 2
    engine.context.lms.get_student_profile.assert_awaited_once()


async def test_observations_return_to_model_without_checkpointed_thinking():
    class Model:
        def bind_tools(self, tools):
            return self
        async def ainvoke(self, messages):
            if isinstance(messages[-1], ToolMessage):
                assert "progress" in messages[-1].content
                return AIMessage(content='{"paragraphs":[{"text":"Try regular practice."}]}')
            return AIMessage(content="", additional_kwargs={"reasoning_content": "PRIVATE-THINKING"},
                             tool_calls=[call("get_learning_snapshot", {"sections": ["profile"]})])
    engine = runtime(Model())
    state = {"messages": [HumanMessage(content="Help me study")], "route": {"course_id": "mine"}}
    output = await engine.decide(state)
    assert output["has_calls"]
    assert "PRIVATE-THINKING" not in str(output)
    await engine.execute(state)
    assert not (await engine.decide(state))["has_calls"]


async def test_partial_failure_preserves_other_evidence():
    engine = runtime()
    engine.context.lms.get_assessment_performance.side_effect = TimeoutError()
    result = json.loads(await engine.by_name["get_learning_snapshot"].ainvoke(
        {"sections": ["profile", "assessments"]}))
    assert result["sections"][0]["evidence"]
    assert result["sections"][1]["error"] == "dependency_failure"
    assert engine.read_failed


async def test_unapproved_plan_never_writes():
    engine = runtime()
    with pytest.raises(PermissionError):
        await engine.by_name["stage_study_plan"].ainvoke({"items": []})
    engine.context.agent.save_study_plan.assert_not_called()


async def test_tool_budget_is_enforced():
    engine = runtime()
    engine.rounds = 4
    engine.messages = [AIMessage(content="", tool_calls=[call("get_learning_snapshot", {"sections": ["profile"]})])]
    output = await engine.execute({})
    assert "budget" in output["messages"][0].content
    engine.context.lms.get_student_profile.assert_not_called()


async def test_invalid_evidence_is_never_streamed(monkeypatch):
    invalid = AIMessage(content=json.dumps({"paragraphs": [{"text": "UNVERIFIED", "evidence_ids": ["fake"]}]}))
    engine = runtime(SimpleNamespace(ainvoke=AsyncMock(return_value=invalid)))
    engine.model.with_structured_output = lambda *a, **k: SimpleNamespace(
        ainvoke=AsyncMock(return_value=json.loads(invalid.content)))
    engine.messages = [invalid]
    events = []
    monkeypatch.setattr("app.agent.react.get_stream_writer", lambda: events.append)
    result = await engine.answer({})
    assert result["outcome"] == "dependency_failure"
    assert "UNVERIFIED" not in str(events)
    assert not engine.context.agent.pending_plans


def test_quantitative_sentences_are_not_treated_as_verified_advice():
    assert qualitative_text("You scored 99%. Practice semantic HTML.") == "Practice semantic HTML."
    assert qualitative_text("Your assignment is due 2026-01-01. Review the supplied notes.") == "Review the supplied notes."
    assert qualitative_text("Build an HTML5 page for 30 minutes.") == "Build an HTML5 page for 30 minutes."


@pytest.mark.parametrize("change", ["unknown_evidence", "wrong_course", "out_of_range", "minutes"])
async def test_invalid_plan_does_not_stage(change):
    engine = runtime()
    engine.allow_plan = True
    engine.horizon_days = 3
    engine.completed_sections = {"profile", "assessments", "deadlines"}
    engine.evidence["record"] = {"kind": "record", "course_id": "mine", "text": "Verified progress",
                                  "fact": {"metric": "progress", "unit": "%", "value": 40}}
    item = {"day_index": 1, "title": "Practice", "minutes": 30,
            "priority": "high", "reason": "Practice foundational topics", "evidence_ids": ["record"]}
    if change == "unknown_evidence":
        item["evidence_ids"] = ["other"]
    elif change == "wrong_course":
        engine.evidence["record"]["course_id"] = "other"
    elif change == "out_of_range":
        item["day_index"] = 20
    elif change == "minutes":
        item["minutes"] = 0
    with pytest.raises((ValueError, PermissionError)):
        await engine.by_name["stage_study_plan"].ainvoke({"horizon_days": 3, "items": [item,
            {**item, "day_index": 2}, {**item, "day_index": 3}]})
    engine.context.agent.save_study_plan.assert_not_called()


async def test_graph_checkpoints_and_sse_never_store_raw_thinking(monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from app.agent.graph import build_agent_graph
    class Model:
        def with_structured_output(self, *args, **kwargs):
            return SimpleNamespace(ainvoke=AsyncMock(return_value={"mode": "retrieve_then_answer",
                "capabilities": ["student_profile"], "course_id": "mine", "reason": "Read own progress"}))
        def bind_tools(self, tools):
            return self
        async def ainvoke(self, messages):
            if isinstance(messages[-1], ToolMessage):
                evidence_id = json.loads(messages[-1].content)["sections"][0]["evidence"][0]["id"]
                return AIMessage(content=json.dumps({"paragraphs": [{"text": "Practice one topic at a time.",
                    "evidence_ids": [evidence_id]}]}), additional_kwargs={"reasoning_content": "RAW-PRIVATE-THOUGHT"})
            return AIMessage(content="", tool_calls=[call("get_learning_snapshot", {"sections": ["profile"]})],
                             additional_kwargs={"reasoning_content": "RAW-PRIVATE-THOUGHT"})
    monkeypatch.setattr("app.agent.graph.chat_model", Model)
    graph = build_agent_graph(runtime().context, MemorySaver())
    config = {"configurable": {"thread_id": "thinking-test"}}
    events = [part async for part in graph.astream({"messages": [HumanMessage(content="Check my progress")]},
                                                  config, stream_mode=["custom", "updates"])]
    snapshots = [snapshot async for snapshot in graph.aget_state_history(config)]
    assert "RAW-PRIVATE-THOUGHT" not in str(events)
    assert "RAW-PRIVATE-THOUGHT" not in str(snapshots)
    assert (await graph.aget_state(config)).values["outcome"] == "answer"


async def test_dependency_errors_do_not_echo_internal_messages():
    engine = runtime()
    engine.context.lms.get_student_profile.side_effect = RuntimeError("password=SECRET")
    result = await engine.by_name["get_learning_snapshot"].ainvoke({"sections": ["profile"]})
    assert "SECRET" not in result
    assert "dependency_failure" in result


async def test_plan_dates_and_quantitative_reasons_are_rendered_by_server():
    from datetime import UTC, date, datetime
    from uuid import uuid4
    engine = runtime()
    engine.allow_plan = True
    engine.horizon_days = 3
    engine.completed_sections = {"profile", "assessments", "deadlines"}
    engine.evidence["record"] = {"kind": "record", "course_id": "mine", "text": "Web progress: 40%",
                                  "fact": {"metric": "progress", "unit": "%", "value": 40}}
    engine.context.agent.save_study_plan.return_value = SimpleNamespace(id=uuid4(), created_at=datetime.now(UTC))
    result = json.loads(await engine.by_name["stage_study_plan"].ainvoke({"horizon_days": 3, "items": [{
        "day_index": day, "title": "Practice HTML", "minutes": 30, "priority": "high",
        "reason": "You scored 99%. Practice the foundations.", "evidence_ids": ["record"]} for day in (1, 2, 3)]}))
    item = result["study_plan"]["items"][0]
    assert item["day"] == date.today().isoformat()
    assert "99%" not in item["reason"]
    assert "40%" in item["reason"]
    assert "Practice the foundations" in item["reason"]
