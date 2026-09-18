"""Development-only model smoke test; not a benchmark or held-out evaluation."""
import argparse
import asyncio
import json
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import text

from app.db import engine
from app.services.agent_service import AgentService
from app.agent import graph as graph_module


async def main(output: Path) -> None:
    async with engine.connect() as connection:
        if await connection.scalar(text("SELECT current_database()")) != "coursepilot_upgrade_test":
            raise RuntimeError("Smoke execution requires the isolated upgrade test database")
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=False)
    original_factory = graph_module.chat_model
    steps = []

    class CaptureModel:
        def __init__(self, model):
            self.model = model
        def bind_tools(self, tools):
            return CaptureModel(self.model.bind_tools(tools))
        def with_structured_output(self, *args, **kwargs):
            return CaptureModel(self.model.with_structured_output(*args, **kwargs))
        async def ainvoke(self, messages):
            try:
                result = await self.model.ainvoke(messages)
            except Exception as exc:
                steps.append({"error_type": type(exc).__name__, "provider_error": str(exc)[:500]})
                raise
            # Capture only the public draft/tool protocol, never additional_kwargs/reasoning.
            steps.append({"content": getattr(result, "content", None),
                          "structured": {k: result[k] for k in ("paragraphs", "outcome") if k in result}
                              if isinstance(result, dict) else None,
                          "tool_calls": getattr(result, "tool_calls", []),
                          "observations": [m.content for m in messages if m.type == "tool"]})
            return result

    graph_module.chat_model = lambda: CaptureModel(original_factory())
    for index, prompt in enumerate([
        "Check my quiz scores, then suggest a concrete activity to improve. Do not create a plan.",
        "Check progress, assessments and deadlines together; explain my next priority.",
        "Create and save a three-day study plan using my progress, assessments and deadlines.",
    ]):
        events = [dict(event=event, data=data) async for event, data in AgentService(MemorySaver()).stream_run(
            user_id="eval-student-alpha", conversation_id=None, message=prompt, course_id="eval-course-web")]
        (output / f"case-{index}.json").write_text(json.dumps({"prompt": prompt, "events": events}, indent=2), encoding="utf-8")
        (output / f"drafts-{index}.json").write_text(json.dumps(steps, indent=2), encoding="utf-8")
        steps.clear()
        final = next((e["data"] for e in events if e["event"] == "final"), {})
        print(json.dumps({"case": index, "outcome": final.get("outcome"),
                          "tools": [e["data"]["name"] for e in events if e["event"] == "tool_status"],
                          "plan": bool(final.get("study_plan"))}), flush=True)
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args().output))
