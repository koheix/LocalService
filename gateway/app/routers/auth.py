"""ログイン・ログアウト・APIキー発行。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Cookie, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    hash_api_key,
    hash_session_token,
    new_api_key,
    new_session_token,
    session_expiry,
    verify_password,
)
from app.config import get_settings
from app.db import get_db
from app.deps import current_user
from app.errors import AuthenticationError, NotFoundError
from app.models import ApiKey, User
from app.models import Session as SessionModel

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    role: str
    created_at: datetime


class ApiKeyCreateRequest(BaseModel):
    name: str


class ApiKeyCreated(BaseModel):
    id: int
    name: str
    key: str
    prefix: str


class ApiKeyOut(BaseModel):
    id: int
    name: str
    prefix: str
    last_used_at: datetime | None
    created_at: datetime


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        created_at=user.created_at,
    )


@router.post("/login")
async def login(
    body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)
) -> UserOut:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise AuthenticationError("メールアドレスまたはパスワードが違います")

    settings = get_settings()
    token = new_session_token()
    db.add(
        SessionModel(
            token_hash=hash_session_token(token),
            user_id=user.id,
            expires_at=session_expiry(settings.session_ttl_hours),
        )
    )
    await db.commit()

    response.set_cookie(
        "session",
        token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_ttl_hours * 3600,
    )
    return _user_out(user)


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    db: AsyncSession = Depends(get_db),
    session: str | None = Cookie(default=None),
) -> None:
    if session:
        token_hash = hash_session_token(session)
        result = await db.execute(select(SessionModel).where(SessionModel.token_hash == token_hash))
        existing = result.scalar_one_or_none()
        if existing is not None:
            await db.delete(existing)
            await db.commit()
    response.delete_cookie("session")


@router.get("/me")
async def me(user: User = Depends(current_user)) -> UserOut:
    return _user_out(user)


@router.post("/api-keys", status_code=201)
async def create_api_key(
    body: ApiKeyCreateRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreated:
    key, prefix = new_api_key()
    api_key = ApiKey(
        user_id=user.id,
        name=body.name,
        key_prefix=prefix,
        key_hash=hash_api_key(key),
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return ApiKeyCreated(id=api_key.id, name=api_key.name, key=key, prefix=prefix)


@router.get("/api-keys")
async def list_api_keys(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[ApiKeyOut]:
    result = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
    )
    return [
        ApiKeyOut(
            id=k.id,
            name=k.name,
            prefix=k.key_prefix,
            last_used_at=k.last_used_at,
            created_at=k.created_at,
        )
        for k in result.scalars()
    ]


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id))
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise NotFoundError("APIキーが見つかりません")
    api_key.revoked_at = datetime.now(UTC)
    await db.commit()
