from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError

from app.config import Settings, get_settings


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    role: str


bearer = HTTPBearer(auto_error=False)


async def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.agent_internal_secret,
            algorithms=["HS256"],
            audience=settings.agent_jwt_audience,
            issuer=settings.agent_jwt_issuer,
            options={"require": ["sub", "role", "iat", "exp", "iss", "aud", "jti"]},
        )
    except InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    user_id = str(payload["sub"]).strip()
    role = str(payload["role"])
    if not user_id or role not in {"STUDENT", "TEACHER"}:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if int(payload["exp"]) - int(payload["iat"]) > 90:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token lifetime is too long")

    return AuthContext(user_id=user_id, role=role)


CurrentAuth = Annotated[AuthContext, Depends(require_auth)]
