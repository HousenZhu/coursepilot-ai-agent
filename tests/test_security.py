from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.config import Settings
from app.security import require_auth


def _settings() -> Settings:
    return Settings(
        agent_internal_secret="test-secret-that-is-at-least-32-characters",
        llm_api_key="test",
    )


def _token(settings: Settings, *, sub: str = "student-1", role: str = "STUDENT") -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": sub,
            "role": role,
            "iss": settings.agent_jwt_issuer,
            "aud": settings.agent_jwt_audience,
            "iat": now,
            "exp": now + timedelta(seconds=60),
            "jti": uuid4().hex,
        },
        settings.agent_internal_secret,
        algorithm="HS256",
    )


@pytest.mark.asyncio
async def test_auth_context_comes_from_signed_token() -> None:
    settings = _settings()
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=_token(settings)
    )
    context = await require_auth(credentials, settings)
    assert context.user_id == "student-1"
    assert context.role == "STUDENT"


@pytest.mark.asyncio
async def test_rejects_token_with_wrong_audience() -> None:
    settings = _settings()
    token = jwt.encode(
        {
            "sub": "student-1",
            "role": "STUDENT",
            "iss": settings.agent_jwt_issuer,
            "aud": "another-service",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(seconds=60),
            "jti": uuid4().hex,
        },
        settings.agent_internal_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as error:
        await require_auth(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), settings)
    assert error.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["ADMIN", "", "student", "TEACHER,STUDENT"])
async def test_rejects_untrusted_roles(role: str) -> None:
    settings = _settings()
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=_token(settings, role=role)
    )
    with pytest.raises(HTTPException) as error:
        await require_auth(credentials, settings)
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_rejects_token_with_excessive_lifetime() -> None:
    settings = _settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "student-1",
            "role": "STUDENT",
            "iss": settings.agent_jwt_issuer,
            "aud": settings.agent_jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=10),
            "jti": uuid4().hex,
        },
        settings.agent_internal_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as error:
        await require_auth(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), settings)
    assert error.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_claim", ["expired", "future", "missing_subject", "wrong_signature"])
async def test_rejects_invalid_identity_credentials(invalid_claim: str) -> None:
    settings = _settings()
    now = datetime.now(UTC)
    payload = {
        "sub": "student-1", "role": "STUDENT", "iss": settings.agent_jwt_issuer,
        "aud": settings.agent_jwt_audience, "iat": now, "exp": now + timedelta(seconds=60),
        "jti": uuid4().hex,
    }
    if invalid_claim == "expired":
        payload.update(iat=now - timedelta(seconds=120), exp=now - timedelta(seconds=60))
    elif invalid_claim == "future":
        payload.update(iat=now + timedelta(seconds=60), exp=now + timedelta(seconds=120))
    elif invalid_claim == "missing_subject":
        del payload["sub"]
    key = "wrong-secret-that-is-at-least-32-characters" if invalid_claim == "wrong_signature" else settings.agent_internal_secret
    token = jwt.encode(payload, key, algorithm="HS256")
    with pytest.raises(HTTPException) as error:
        await require_auth(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), settings)
    assert error.value.status_code == 401
