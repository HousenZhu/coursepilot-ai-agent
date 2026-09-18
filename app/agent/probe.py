"""Synthetic capability check. Never prints reasoning or uses student records."""
import asyncio
import json

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from app.agent.models import chat_model
from app.config import get_settings


async def check_model() -> dict[str, bool]:
    @tool
    def read_probe_value() -> str:
        """Read the unknown validation value required to answer the request."""
        return "COURSEPILOT-PROBE-719"

    settings = get_settings()
    model = chat_model()
    messages = [SystemMessage(content="Call read_probe_value to discover the value. After receiving it, "
                              "return exactly that value. Never guess it."),
                HumanMessage(content="What is the validation value?")]
    async with asyncio.timeout(settings.run_timeout_seconds):
        response = await model.bind_tools([read_probe_value]).ainvoke(messages)
        if len(response.tool_calls) != 1 or response.tool_calls[0]["name"] != "read_probe_value":
            raise RuntimeError("Model capability check failed: native tool call missing")
        call = response.tool_calls[0]
        followup = await model.ainvoke([*messages, response,
            ToolMessage(content=read_probe_value.invoke({}), tool_call_id=call["id"])])
        thinking = bool(response.additional_kwargs.get("reasoning_content")
                        or followup.additional_kwargs.get("reasoning_content"))
        if settings.llm_provider == "ollama" and not settings.llm_disable_thinking and not thinking:
            raise RuntimeError("Model capability check failed: reasoning field missing")
        if str(followup.content).strip() != "COURSEPILOT-PROBE-719":
            raise RuntimeError("Model capability check failed: observation was not used")
    return {"tool_call": True, "observation_used": True, "thinking_field": thinking}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(check_model())))
