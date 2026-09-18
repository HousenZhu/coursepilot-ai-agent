import json
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph
from opentelemetry import trace

from app.agent.models import chat_model
from app.agent.react import ReactRuntime
from app.config import get_settings
from app.observability.metrics import STAGE_LATENCY
from app.routing import ROUTER_PROMPT, IntentRoute, enforce_route_policy
from app.tools import ToolContext


class AgentState(MessagesState):
    course_id: str | None
    tool_iterations: int
    has_calls: bool
    route: dict[str, Any]
    answer: str
    citations: list[dict[str, Any]]
    study_plan: dict[str, Any] | None
    facts: list[dict[str, Any]]
    outcome: str
    grounded: bool


def build_agent_graph(context: ToolContext, checkpointer: Any) -> Any:
    model = chat_model()
    runtime = ReactRuntime(context, model, get_settings().max_tool_iterations)
    router = model.with_structured_output(IntentRoute, method="json_mode")

    async def validate(state: AgentState) -> dict[str, Any]:
        if context.course_id and not await context.lms.student_has_course(context.user_id, context.course_id):
            raise PermissionError("Course is not accessible")
        return {"tool_iterations": 0, "has_calls": False, "answer": "", "citations": [],
                "study_plan": None, "facts": [], "outcome": "answer", "grounded": False}

    async def policy(state: AgentState) -> dict[str, Any]:
        profile = await context.lms.get_student_profile(context.user_id)
        courses = profile.get("courses", [])
        history = [m for m in state["messages"] if isinstance(m, HumanMessage)
                   or isinstance(m, AIMessage) and not m.tool_calls][-8:]
        trusted = json.dumps({"selected_course": context.course_id,
                              "enrolled_courses": [{"course_id": c["course_id"], "title": c["title"]}
                                                   for c in courses]})
        raw = await router.ainvoke([
            SystemMessage(content=ROUTER_PROMPT + "\nTrusted scope: " + trusted),
            HumanMessage(content=json.dumps([{"role": "user" if isinstance(m, HumanMessage) else "assistant",
                                             "content": str(m.content)} for m in history])),
        ])
        result = enforce_route_policy(IntentRoute.model_validate(raw))
        selected = context.course_id or result.course_id
        if selected and not await context.lms.student_has_course(context.user_id, selected):
            result = result.model_copy(update={"mode": "refuse", "capabilities": [], "mutates_state": False})
        if not selected and len(courses) == 1:
            selected = courses[0]["course_id"]
        return {"route": result.model_copy(update={"course_id": selected}).model_dump()}

    async def terminal(state: AgentState) -> dict[str, Any]:
        outcome = state["route"]["mode"]
        answer = ("I can only access records for the authenticated account. I cannot fulfill that request."
                  if outcome == "refuse" else "Please specify the course or the learning help you need.")
        get_stream_writer()({"event": "token", "data": {"delta": answer}})
        return {"answer": answer, "outcome": outcome, "messages": [AIMessage(content=answer)], "grounded": True}

    def traced(name: str, node: Any) -> Any:
        async def invoke(state: AgentState) -> dict[str, Any]:
            started = time.perf_counter()
            try:
                with trace.get_tracer("coursepilot.agent").start_as_current_span(name):
                    return await node(state)
            finally:
                STAGE_LATENCY.labels(stage=name).observe(time.perf_counter() - started)
        return invoke

    builder = StateGraph(AgentState)
    for name, node in (("validate", validate), ("route_request", policy), ("agent", runtime.decide),
                       ("tools", runtime.execute), ("answer", runtime.answer), ("terminal", terminal)):
        builder.add_node(name, traced(name, node))
    builder.add_edge(START, "validate")
    builder.add_edge("validate", "route_request")
    builder.add_conditional_edges("route_request", lambda state:
                                 "terminal" if state["route"]["mode"] in {"refuse", "clarify"} else "agent")
    builder.add_conditional_edges("agent", lambda state: "tools" if state["has_calls"] else "answer")
    builder.add_edge("tools", "agent")
    builder.add_edge("answer", END)
    builder.add_edge("terminal", END)
    return builder.compile(checkpointer=checkpointer)
