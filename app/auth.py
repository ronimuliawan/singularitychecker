from __future__ import annotations

from fastapi import HTTPException, Request, status
from passlib.context import CryptContext


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_session_user(request: Request) -> str | None:
    value = request.session.get("user")
    if isinstance(value, str) and value:
        return value
    return None


def require_api_user(request: Request) -> str:
    user = get_session_user(request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user
