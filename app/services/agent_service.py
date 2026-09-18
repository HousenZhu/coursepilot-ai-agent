import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent import build_agent_graph
from app.config import get_settings
from app.observability.logging import get_logger
from app.observability.metrics import AGENT_LATENCY, AGENT_RUNS, AGENT_TTFT
from app.observability.usage import UsageCallback
from app.repositories.agent import AgentRepository, RunLease
from app.repositories.lms import LMSRepository
from app.schemas import AgentFinalResponse, Citation, StudyPlan
from app.tools import ToolContext

logger = get_logger()
_slots = asyncio.Semaphore(get_settings().max_concurrent_runs)


def _stream_part(part: Any) -> tuple[str | None, Any]:
    if isinstance(part, dict) and "type" in part:
        return str(part["type"]), part.get("data")
    if isinstance(part, tuple) and len(part) == 2:
        return str(part[0]), part[1]
    return None, None


class AgentService:
    def __init__(self, checkpointer: Any) -> None:
        self.checkpointer = checkpointer
        self.agent_repository = AgentRepository(stage_plans=True)
        self.lms_repository = LMSRepository()

    async def stream_run(self, *, user_id: str, conversation_id: UUID | None, message: str,
                         course_id: str | None, lease: RunLease | None = None) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        started = time.perf_counter()
        lease = lease or await self.agent_repository.reserve_run(user_id, conversation_id, message, course_id, None)
        run = lease.run
        calls: list[dict[str, Any]] = []
        first = False
        completed = False
        try:
            if run.status == "completed":
                yield "final", run.final_response or {}
                completed = True
                return
            async with asyncio.timeout(get_settings().run_timeout_seconds), _slots:
                graph = build_agent_graph(ToolContext(user_id, course_id, self.lms_repository, self.agent_repository), self.checkpointer)
                # Durable checkpoints are for inspection only after failure; reconstruct context from committed history.
                history = await self.agent_repository.get_messages(user_id, run.conversation_id)
                graph_input = {"messages": [HumanMessage(content=m.content) if m.role == "user"
                                           else AIMessage(content=m.content) for m in history][-16:],
                               "course_id": course_id, "tool_iterations": 0}
                usage_callback = UsageCallback()
                config = {"configurable": {"thread_id": str(run.id)}, "callbacks": [usage_callback],
                          "metadata": {"trace_id": run.trace_id}}
                async for raw in graph.astream(graph_input, config=config, stream_mode=["custom", "updates"], version="v2"):
                    mode, data = _stream_part(raw)
                    if mode == "custom" and isinstance(data, dict):
                        event = data.get("event")
                        if event == "token":
                            if not first:
                                AGENT_TTFT.observe(time.perf_counter() - started)
                                first = True
                            yield "token", data["data"]
                        continue
                    if mode != "updates" or not isinstance(data, dict):
                        continue
                    for output in (data.get("agent") or {}).get("messages", []):
                        if isinstance(output, AIMessage):
                            for call in output.tool_calls:
                                calls.append({"id": call["id"], "name": call["name"],
                                              "arg_keys": sorted(call["args"])})
                                yield "tool_status", {"id": call["id"], "name": call["name"], "status": "started"}
                    for output in (data.get("tools") or {}).get("messages", []):
                        if isinstance(output, ToolMessage):
                            payload = json.loads(str(output.content))
                            capabilities = [s["section"] for s in payload.get("sections", []) if not s.get("error")]
                            yield "tool_status", {"id": output.tool_call_id, "name": output.name,
                                "status": "failed" if payload.get("error") else "completed",
                                "capabilities": capabilities,
                                "failed_sections": [s["section"] for s in payload.get("sections", []) if s.get("error")]}
                snapshot = await graph.aget_state(config)
                values = snapshot.values
                final = AgentFinalResponse(
                    run_id=run.id, conversation_id=run.conversation_id, trace_id=run.trace_id,
                    answer_markdown=values.get("answer", ""), outcome=values.get("outcome", "answer"),
                    facts=values.get("facts", []),
                    citations=[Citation.model_validate(c) for c in values.get("citations", [])],
                    study_plan=StudyPlan.model_validate(values["study_plan"]) if values.get("study_plan") else None)
                body = final.model_dump(mode="json")
                usage = usage_callback.totals
                await self.agent_repository.complete_run(run.id, body, int((time.perf_counter() - started) * 1000), calls, usage)
                completed = True
                AGENT_RUNS.labels(status="completed").inc()
                AGENT_LATENCY.observe(time.perf_counter() - started)
                yield "final", body
        except (asyncio.CancelledError, GeneratorExit):
            if not completed:
                await asyncio.shield(self._fail(run.id, started, calls, "cancelled", "client_disconnected"))
            raise
        except Exception as exc:
            if not completed:
                await self._fail(run.id, started, calls, "failed", type(exc).__name__)
            logger.warning("agent_run_failed", trace_id=run.trace_id, error_type=type(exc).__name__)
            yield "error", {"code": type(exc).__name__, "message": "Request did not finish normally. Check run status before retrying.",
                            "run_id": str(run.id), "trace_id": run.trace_id}
        finally:
            await asyncio.shield(lease.close())

    async def _fail(self, run_id: UUID, started: float, calls: list[dict[str, Any]], status: str, code: str) -> None:
        self.agent_repository.pending_plans.clear()
        await self.agent_repository.finish_run(run_id, status=status, latency_ms=int((time.perf_counter() - started) * 1000),
                                               tool_calls=calls, error_code=code)
        AGENT_RUNS.labels(status=status).inc()

    @staticmethod
    def _usage(messages: list[Any]) -> dict[str, int]:
        totals = {"input_tokens": 0, "output_tokens": 0}
        for message in messages:
            usage = getattr(message, "usage_metadata", None) or {}
            for name in totals:
                totals[name] += int(usage.get(name, 0))
        return totals
