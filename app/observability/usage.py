from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult
from prometheus_client import Counter

MODEL_CALLS = Counter("coursepilot_model_calls_total", "Completed model invocations")
MODEL_TOKENS = Counter("coursepilot_model_tokens_total", "Reported model tokens", ["direction"])


class UsageCallback(AsyncCallbackHandler):
    def __init__(self) -> None:
        self.totals = {"input_tokens": 0, "output_tokens": 0}

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        MODEL_CALLS.inc()
        for generation in response.generations:
            if not generation:
                continue
            usage = getattr(getattr(generation[0], "message", None), "usage_metadata", None) or {}
            for key in self.totals:
                value = int(usage.get(key, 0))
                self.totals[key] += value
                MODEL_TOKENS.labels(direction=key).inc(value)
