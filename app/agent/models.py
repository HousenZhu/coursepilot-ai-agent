"""Provider-specific reasoning configuration; no provider-neutral thinking flags."""
from typing import Any

from langchain_openai import ChatOpenAI

from app.config import get_settings


def chat_model() -> Any:
    settings = get_settings()
    if settings.llm_provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            base_url=settings.llm_base_url.removesuffix("/v1"),
            model=settings.llm_model, reasoning=not settings.llm_disable_thinking,
            temperature=settings.llm_temperature, num_ctx=settings.llm_context_size,
            num_predict=settings.llm_max_tokens,
            client_kwargs={"timeout": settings.llm_timeout_seconds},
        )
    return ChatOpenAI(
        api_key=settings.llm_api_key, base_url=settings.llm_base_url,
        model=settings.llm_model, temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds, max_tokens=settings.llm_max_tokens,
        max_retries=2,
    )
