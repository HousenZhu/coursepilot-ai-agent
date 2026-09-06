import pytest
from pydantic import ValidationError

from app.schemas import AgentRunRequest


def test_agent_request_does_not_accept_user_id() -> None:
    with pytest.raises(ValidationError):
        AgentRunRequest(message="Show my progress", user_id="another-user")


@pytest.mark.parametrize("length", [0, 4001])
def test_agent_request_rejects_invalid_message_length(length: int) -> None:
    with pytest.raises(ValidationError):
        AgentRunRequest(message="x" * length)


def test_agent_request_accepts_bounded_message() -> None:
    request = AgentRunRequest(message="Create a study plan")
    assert request.message == "Create a study plan"
