import json
import re
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.prompts import SYSTEM_PROMPT
from app.config import get_settings
from app.observability.logging import get_logger
from app.routing import (
    ROUTER_PROMPT,
    IntentRoute,
    enforce_explicit_evidence_request,
    enforce_route_policy,
    required_evidence_kinds,
    required_tool_names,
)
from app.tools import ToolContext, build_learning_tools


logger = get_logger()


class AgentState(MessagesState):
    course_id: str | None
    turn_start_index: int
    tool_iterations: int
    route: dict[str, Any]
    required_tools: list[str]
    answer: str
    citations: list[dict[str, Any]]
    study_plan: dict[str, Any] | None
    grounded: bool


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


def _extract_tool_artifacts(
    messages: Sequence[BaseMessage],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    citations: list[dict[str, Any]] = []
    study_plan: dict[str, Any] | None = None
    seen: set[tuple[str, int | None, str]] = set()
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = json.loads(_message_text(message))
        except (json.JSONDecodeError, TypeError):
            continue
        for citation in payload.get("citations", []):
            key = (citation["content_id"], citation.get("page"), citation["excerpt"])
            if key not in seen:
                citations.append(citation)
                seen.add(key)
        if payload.get("study_plan"):
            study_plan = payload["study_plan"]
    return citations, study_plan


def _tool_kinds(messages: Sequence[BaseMessage]) -> set[str]:
    kinds: set[str] = set()
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = json.loads(_message_text(message))
        except (json.JSONDecodeError, TypeError):
            continue
        if not payload.get("error") and isinstance(payload.get("kind"), str):
            kinds.add(payload["kind"])
    return kinds


def _verified_assessment_deadline_summary(messages: Sequence[BaseMessage]) -> str | None:
    lowest_quiz: dict[str, Any] | None = None
    nearest_deadline: dict[str, Any] | None = None
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = json.loads(_message_text(message))
        except (json.JSONDecodeError, TypeError):
            continue
        if payload.get("kind") == "assessment_performance":
            attempts = [
                item
                for item in payload.get("data", {}).get("quiz_attempts", [])
                if item.get("score") is not None
            ]
            if attempts:
                lowest_quiz = min(attempts, key=lambda item: float(item["score"]))
        elif payload.get("kind") == "deadlines":
            deadlines = payload.get("data", [])
            if deadlines:
                nearest_deadline = deadlines[0]
    if not lowest_quiz or not nearest_deadline:
        return None
    deadline = str(nearest_deadline.get("deadline", ""))[:10]
    return (
        f"Verified records: the lowest quiz score is {lowest_quiz['score']}% "
        f"for {lowest_quiz['quiz_title']}; the nearest deadline is "
        f"{nearest_deadline['title']} on {deadline}."
    )


def _route(state: AgentState) -> IntentRoute:
    return IntentRoute.model_validate(state["route"])


def _requirements_satisfied(
    route: IntentRoute,
    messages: Sequence[BaseMessage],
) -> bool:
    kinds = _tool_kinds(messages)
    return all(bool(kinds & allowed) for allowed in required_evidence_kinds(route))


def build_agent_graph(context: ToolContext, checkpointer: Any) -> Any:
    settings = get_settings()
    extra_body = {"think": False} if settings.llm_disable_thinking else None
    no_think_suffix = "\n\n/no_think" if settings.llm_disable_thinking else ""
    system_prompt = f"{SYSTEM_PROMPT}{no_think_suffix}"
    router_prompt = f"{ROUTER_PROMPT}{no_think_suffix}"
    tools = build_learning_tools(context)
    tools_by_name = {tool.name: tool for tool in tools}
    model = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
        streaming=True,
        temperature=settings.llm_temperature,
        extra_body=extra_body,
        max_tokens=settings.llm_max_tokens,
    )
    router = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
        streaming=False,
        temperature=0,
        extra_body=extra_body,
        max_tokens=settings.llm_max_tokens,
    ).with_structured_output(IntentRoute)

    async def validate_node(state: AgentState) -> dict[str, Any]:
        return {
            "course_id": state.get("course_id"),
            "turn_start_index": max(len(state["messages"]) - 1, 0),
            "tool_iterations": 0,
            "route": {},
            "required_tools": [],
            "citations": [],
            "study_plan": None,
            "grounded": False,
        }

    async def route_request_node(state: AgentState) -> dict[str, Any]:
        conversation = [
            message
            for message in state["messages"]
            if isinstance(message, HumanMessage)
            or (isinstance(message, AIMessage) and not message.tool_calls)
        ][-20:]
        try:
            raw_route = await router.ainvoke(
                [SystemMessage(content=router_prompt), *conversation]
            )
            route = enforce_route_policy(IntentRoute.model_validate(raw_route))
            latest_user = next(
                (message for message in reversed(conversation) if isinstance(message, HumanMessage)),
                None,
            )
            route = enforce_explicit_evidence_request(
                route, _message_text(latest_user) if latest_user else ""
            )
        except Exception as exc:
            logger.warning("intent_router_failed", error_type=type(exc).__name__)
            route = IntentRoute(
                mode="clarify",
                subject="unspecified",
                needs_clarification=True,
                reason="The request could not be classified safely.",
            )
        return {
            "route": route.model_dump(mode="json"),
            "required_tools": sorted(required_tool_names(route)),
        }

    def route_after_request(
        state: AgentState,
    ) -> Literal["answer", "planner", "terminal"]:
        mode = _route(state).mode
        if mode in {"direct_answer", "conversation_answer"}:
            return "answer"
        if mode in {"retrieve_then_answer", "execute_then_answer"}:
            return "planner"
        return "terminal"

    async def planner_node(state: AgentState) -> dict[str, Any]:
        route = _route(state)
        turn_messages = state["messages"][state.get("turn_start_index", 0) :]
        available_kinds = _tool_kinds(turn_messages)
        missing_capabilities = [
            capability
            for capability, allowed in zip(
                route.capabilities,
                required_evidence_kinds(route),
                strict=True,
            )
            if not available_kinds.intersection(allowed)
        ]
        missing_tools = [
            tools_by_name[name]
            for name in sorted(
                required_tool_names(
                    route.model_copy(update={"capabilities": missing_capabilities})
                )
            )
        ]
        planner_prompt = (
            "You are the tool-planning stage. Do not answer the user yet. "
            "Call every tool needed for these missing capabilities: "
            f"{', '.join(missing_capabilities)}. Only call the tools provided to you. "
            "Do not repeat a successful tool call from this turn."
        )
        response = await model.bind_tools(missing_tools).ainvoke(
            [
                SystemMessage(content=system_prompt),
                SystemMessage(content=planner_prompt),
                *state["messages"],
            ]
        )
        return {
            "messages": [response],
            "tool_iterations": state.get("tool_iterations", 0) + 1,
        }

    def route_after_planner(state: AgentState) -> Literal["tools", "verify"]:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return "verify"
        allowed_tools = set(state.get("required_tools", []))
        calls_are_allowed = all(
            str(call.get("name")) in allowed_tools for call in last.tool_calls
        )
        if (
            calls_are_allowed
            and state.get("tool_iterations", 0) <= settings.max_tool_iterations
        ):
            return "tools"
        return "verify"

    def route_after_tools(state: AgentState) -> Literal["answer", "planner", "verify"]:
        turn_messages = state["messages"][state.get("turn_start_index", 0) :]
        if _requirements_satisfied(_route(state), turn_messages):
            return "answer"
        if state.get("tool_iterations", 0) < settings.max_tool_iterations:
            return "planner"
        return "verify"

    async def answer_node(state: AgentState) -> dict[str, Any]:
        route = _route(state)
        contract = (
            f"The validated request mode is {route.mode}. "
            f"Required capabilities are: {', '.join(route.capabilities) or 'none'}. "
            "Answer the user now. Do not call tools. For personal LMS facts, use only "
            "successful tool results in this turn. Conversation history is not evidence "
            "of LMS facts. If both assessments and deadlines were requested, explicitly "
            "name the lowest recorded score and the nearest deadline item."
        )
        response = await model.ainvoke(
            [
                SystemMessage(content=system_prompt),
                SystemMessage(content=contract),
                *state["messages"],
            ]
        )
        return {"messages": [response]}

    async def terminal_node(state: AgentState) -> dict[str, Any]:
        route = _route(state)
        if route.mode == "refuse":
            answer = (
                "I can only access learning data and supported actions for the "
                "authenticated account, so I cannot complete that request."
            )
        else:
            answer = (
                "Could you clarify whether you want a general explanation, information "
                "from this conversation, or data from your CoursePilot learning records?"
            )
        return {
            "messages": [AIMessage(content=answer)],
            "answer": answer,
            "citations": [],
            "study_plan": None,
            "grounded": True,
        }

    async def verify_node(state: AgentState) -> dict[str, Any]:
        turn_messages = state["messages"][state.get("turn_start_index", 0) :]
        citations, study_plan = _extract_tool_artifacts(turn_messages)
        last_ai = next(
            (message for message in reversed(turn_messages) if isinstance(message, AIMessage)),
            None,
        )
        answer = _message_text(last_ai) if last_ai else ""
        grounded = _requirements_satisfied(_route(state), turn_messages)

        if not answer:
            answer = "I could not complete that request within the safe tool-call limit. Please narrow the question."
        elif not grounded:
            answer = (
                "I could not retrieve all of the learning records required for this "
                "request, so I will not guess. Please try again or narrow the request."
            )
        elif citations and "[Source" not in answer:
            source_labels = ", ".join(
                f"[Source {index}] {citation['title']}"
                for index, citation in enumerate(citations, start=1)
            )
            answer = f"{answer}\n\nSources: {source_labels}"
        elif not citations:
            answer = re.sub(r"\s*\[Source\s+\d+\]", "", answer, flags=re.I)

        route = _route(state)
        if {"assessment_records", "deadlines"}.issubset(route.capabilities):
            summary = _verified_assessment_deadline_summary(turn_messages)
            if summary:
                answer = f"{answer}\n\n{summary}"

        referenced_sources = {
            int(match)
            for match in re.findall(r"\[Source\s+(\d+)\]", answer, re.I)
        }
        if referenced_sources:
            citations = [
                citation
                for index, citation in enumerate(citations, start=1)
                if index in referenced_sources
            ]

        return {
            "answer": answer,
            "citations": citations,
            "study_plan": study_plan,
            "grounded": grounded,
        }

    builder = StateGraph(AgentState)
    builder.add_node("validate", validate_node)
    builder.add_node("route_request", route_request_node)
    builder.add_node("planner", planner_node)
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    builder.add_node("answer", answer_node)
    builder.add_node("terminal", terminal_node)
    builder.add_node("verify", verify_node)
    builder.add_edge(START, "validate")
    builder.add_edge("validate", "route_request")
    builder.add_conditional_edges(
        "route_request",
        route_after_request,
        {"answer": "answer", "planner": "planner", "terminal": "terminal"},
    )
    builder.add_conditional_edges(
        "planner",
        route_after_planner,
        {"tools": "tools", "verify": "verify"},
    )
    builder.add_conditional_edges(
        "tools",
        route_after_tools,
        {"answer": "answer", "planner": "planner", "verify": "verify"},
    )
    builder.add_edge("answer", "verify")
    builder.add_edge("terminal", END)
    builder.add_edge("verify", END)
    return builder.compile(checkpointer=checkpointer)
