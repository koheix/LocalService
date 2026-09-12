"""FastAPI Depends（現在ユーザー取得、権限チェック）。"""

from datetime import UTC, datetime

from fastapi import Cookie, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_session_token, verify_api_key
from app.db import get_db
from app.errors import AuthenticationError, PermissionError_
from app.models import ApiKey, User
from app.models import Session as SessionModel


async def _user_from_session(db: AsyncSession, token: str) -> User | None:
    token_hash = hash_session_token(token)
    result = await db.execute(select(SessionModel).where(SessionModel.token_hash == token_hash))
    session = result.scalar_one_or_none()
    if session is None:
        return None
    if session.expires_at < datetime.now(UTC):
        await db.delete(session)
        await db.commit()
        return None
    return await db.get(User, session.user_id)


async def _user_from_api_key(db: AsyncSession, key: str) -> User | None:
    if len(key) < 8:
        return None
    prefix = key[:8]
    result = await db.execute(
        select(ApiKey).where(ApiKey.key_prefix == prefix, ApiKey.revoked_at.is_(None))
    )
    for api_key in result.scalars():
        if verify_api_key(key, api_key.key_hash):
            api_key.last_used_at = datetime.now(UTC)
            await db.commit()
            return await db.get(User, api_key.user_id)
    return None


async def current_user(
    db: AsyncSession = Depends(get_db),
    session: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> User:
    user: User | None = None
    if authorization and authorization.lower().startswith("bearer "):
        user = await _user_from_api_key(db, authorization[7:].strip())
    elif session:
        user = await _user_from_session(db, session)

    if user is None or not user.is_active:
        raise AuthenticationError("認証が必要です")
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise PermissionError_("管理者権限が必要です")
    return user
