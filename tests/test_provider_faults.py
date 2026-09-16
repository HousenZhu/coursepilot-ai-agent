import httpx
import pytest
from langchain_openai import ChatOpenAI
from openai import BadRequestError


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503, 400])
async def test_provider_retries_only_transient_errors(status: int) -> None:
    attempts = []
    def respond(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) <= 2:
            return httpx.Response(status, json={"error": {"message": "injected", "type": "test"}},
                                  headers={"retry-after-ms": "1"})
        return httpx.Response(200, json={"id": "completion-test", "model": "test", "object": "chat.completion",
            "created": 0, "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "Recovered"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ChatOpenAI(api_key="test", base_url="http://provider.test/v1", model="test",
                           http_async_client=client, max_retries=2)
        if status == 400:
            with pytest.raises(BadRequestError):
                await model.ainvoke("hello")
            assert len(attempts) == 1
        else:
            assert (await model.ainvoke("hello")).content == "Recovered"
            assert len(attempts) == 3
