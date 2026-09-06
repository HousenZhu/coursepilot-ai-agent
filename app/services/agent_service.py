import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from app.agent import build_agent_graph
from app.observability.logging import get_logger
from app.observability.metrics import AGENT_LATENCY, AGENT_RUNS, AGENT_TTFT
from app.repositories.agent import AgentRepository
from app.repositories.lms import LMSRepository
from app.schemas import AgentFinalResponse, Citation, StudyPlan
from app.tools import ToolContext


logger = get_logger()


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return ""


def _stream_part(part: Any) -> tuple[str | None, Any]:
    if isinstance(part, dict) and "type" in part:
        return str(part["type"]), part.get("data")
    if isinstance(part, tuple) and len(part) == 2:
        return str(part[0]), part[1]
    return None, None


class AgentService:
    def __init__(self, checkpointer: Any) -> None:
        self.checkpointer = checkpointer
        self.agent_repository = AgentRepository()
        self.lms_repository = LMSRepository()

    async def stream_run(
        self,
        *,
        user_id: str,
        conversation_id: UUID | None,
        message: str,
        course_id: str | None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        started = time.perf_counter()
        trace_id = uuid4().hex
        run_id: UUID | None = None
        tool_calls: list[dict[str, Any]] = []
        started_tools: set[str] = set()
        completed_tools: set[str] = set()
        first_token_recorded = False

        try:
            conversation = await self.agent_repository.get_or_create_conversation(
                user_id, conversation_id
            )
            run_id = await self.agent_repository.start_run(conversation.id, user_id, trace_id)
            await self.agent_repository.add_message(conversation.id, "user", message)

            graph = build_agent_graph(
                ToolContext(
                    user_id=user_id,
                    course_id=course_id,
                    lms=self.lms_repository,
                    agent=self.agent_repository,
                ),
                self.checkpointer,
            )
            config = {
                "configurable": {"thread_id": str(conversation.id)},
                "metadata": {"trace_id": trace_id, "user_id": user_id},
            }
            graph_input = {
                "messages": [HumanMessage(content=message)],
                "course_id": course_id,
                "tool_iterations": 0,
            }

            async for raw_part in graph.astream(
                graph_input,
                config=config,
                stream_mode=["messages", "updates"],
                version="v2",
            ):
                mode, data = _stream_part(raw_part)
                if mode == "messages" and isinstance(data, tuple) and len(data) == 2:
                    chunk, metadata = data
                    if (
                        isinstance(chunk, AIMessageChunk)
                        and metadata.get("langgraph_node") == "answer"
                    ):
                        delta = _content_text(chunk.content)
                        if delta:
                            if not first_token_recorded:
                                AGENT_TTFT.observe(time.perf_counter() - started)
                                first_token_recorded = True
                            yield "token", {"delta": delta}

                if mode != "updates" or not isinstance(data, dict):
                    continue

                planner_update = data.get("planner") or {}
                for output_message in planner_update.get("messages", []):
                    if not isinstance(output_message, AIMessage):
                        continue
                    for call in output_message.tool_calls:
                        call_id = str(call.get("id") or f"{call['name']}:{len(tool_calls)}")
                        if call_id in started_tools:
                            continue
                        started_tools.add(call_id)
                        tool_calls.append(
                            {
                                "id": call_id,
                                "name": call["name"],
                                "arg_keys": sorted(call.get("args", {}).keys()),
                            }
                        )
                        yield "tool_status", {
                            "id": call_id,
                            "name": call["name"],
                            "status": "started",
                        }

                tools_update = data.get("tools") or {}
                for output_message in tools_update.get("messages", []):
                    if not isinstance(output_message, ToolMessage):
                        continue
                    call_id = str(output_message.tool_call_id)
                    if call_id in completed_tools:
                        continue
                    completed_tools.add(call_id)
                    yield "tool_status", {
                        "id": call_id,
                        "name": output_message.name or "tool",
                        "status": "completed",
                    }

            snapshot = await graph.aget_state(config)
            values = snapshot.values
            answer = str(values.get("answer") or "")
            citations = [Citation.model_validate(item) for item in values.get("citations", [])]
            raw_plan = values.get("study_plan")
            study_plan = StudyPlan.model_validate(raw_plan) if raw_plan else None
            final = AgentFinalResponse(
                conversation_id=conversation.id,
                answer_markdown=answer,
                citations=citations,
                study_plan=study_plan,
                suggested_actions=self._suggested_actions(citations, study_plan),
                trace_id=trace_id,
            )
            await self.agent_repository.add_message(conversation.id, "assistant", answer)

            latency_ms = int((time.perf_counter() - started) * 1000)
            turn_start = int(values.get("turn_start_index", 0))
            usage = self._usage(values.get("messages", [])[turn_start:])
            await self.agent_repository.finish_run(
                run_id,
                status="completed",
                latency_ms=latency_ms,
                tool_calls=tool_calls,
                prompt_tokens=usage.get("input_tokens"),
                completion_tokens=usage.get("output_tokens"),
            )
            AGENT_RUNS.labels(status="completed").inc()
            AGENT_LATENCY.observe(latency_ms / 1000)
            logger.info(
                "agent_run_completed",
                trace_id=trace_id,
                conversation_id=str(conversation.id),
                latency_ms=latency_ms,
                tool_names=[call["name"] for call in tool_calls],
            )
            yield "final", final.model_dump(mode="json")
        except asyncio.CancelledError:
            await self._record_failure(run_id, started, tool_calls, "client_disconnected")
            raise
        except Exception as exc:
            await self._record_failure(run_id, started, tool_calls, type(exc).__name__)
            logger.exception("agent_run_failed", trace_id=trace_id, error_type=type(exc).__name__)
            yield "error", {
                "code": "agent_run_failed",
                "message": "The learning assistant could not complete this request.",
                "trace_id": trace_id,
            }

    async def _record_failure(
        self,
        run_id: UUID | None,
        started: float,
        tool_calls: list[dict[str, Any]],
        error_code: str,
    ) -> None:
        latency_ms = int((time.perf_counter() - started) * 1000)
        if run_id is not None:
            await self.agent_repository.finish_run(
                run_id,
                status="failed",
                latency_ms=latency_ms,
                tool_calls=tool_calls,
                error_code=error_code,
            )
        AGENT_RUNS.labels(status="failed").inc()
        AGENT_LATENCY.observe(latency_ms / 1000)

    @staticmethod
    def _usage(messages: list[Any]) -> dict[str, int]:
        totals = {"input_tokens": 0, "output_tokens": 0}
        for message in messages:
            usage = getattr(message, "usage_metadata", None) or {}
            totals["input_tokens"] += int(usage.get("input_tokens", 0))
            totals["output_tokens"] += int(usage.get("output_tokens", 0))
        return totals

    @staticmethod
    def _suggested_actions(
        citations: list[Citation], study_plan: StudyPlan | None
    ) -> list[str]:
        actions: list[str] = []
        if study_plan is None:
            actions.append("Create a 7-day study plan")
        if not citations:
            actions.append("Ask a question about an enrolled course PDF")
        actions.append("Review upcoming deadlines")
        return actions[:3]
