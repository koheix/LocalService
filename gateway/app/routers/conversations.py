"""会話(プレイグラウンド)のCRUD。自分の会話のみ操作できる。

他ユーザーの会話IDを指定した場合は、存在の有無を漏らさないため
403ではなく404を返す。
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backends import get_chat_backend
from app.config import get_settings
from app.db import async_session_maker, get_db
from app.deps import AuthContext, current_auth, current_user
from app.errors import (
    InvalidRequestError,
    NotFoundError,
    PermissionError_,
    RateLimitError,
)
from app.limits import concurrency_slots, rate_limiter
from app.models import Conversation, Message, Model, ModelPermission, Quota, UsageLog, User
from app.sse import rewrite_chunk, with_stream_usage

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

logger = structlog.get_logger()


class ConversationCreateRequest(BaseModel):
    title: str = ""
    model: str | None = None  # served_name。内部IDはクライアントに見せない(D-003)
    system_prompt: str = ""
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int | None = None


class ConversationUpdateRequest(BaseModel):
    title: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None


class ConversationOut(BaseModel):
    id: int
    title: str
    model: str | None
    system_prompt: str
    temperature: float
    top_p: float
    max_tokens: int | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime


def _conversation_out(c: Conversation, served_name: str | None) -> ConversationOut:
    return ConversationOut(
        id=c.id,
        title=c.title,
        model=served_name,
        system_prompt=c.system_prompt,
        temperature=c.temperature,
        top_p=c.top_p,
        max_tokens=c.max_tokens,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


async def _get_owned_conversation(
    db: AsyncSession, user: User, conversation_id: int
) -> Conversation:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise NotFoundError(f"会話が見つかりません: {conversation_id}")
    return conversation


async def _served_name_of(db: AsyncSession, model_id: int | None) -> str | None:
    if model_id is None:
        return None
    model = await db.get(Model, model_id)
    return model.served_name if model else None


async def _resolve_model(db: AsyncSession, served_name: str | None) -> Model | None:
    """served_name からモデル行を解決する。指定されたのに見つからなければ404。"""
    if served_name is None:
        return None
    result = await db.execute(
        select(Model).where(Model.served_name == served_name, Model.is_enabled.is_(True))
    )
    model = result.scalar_one_or_none()
    if model is None:
        raise NotFoundError(f"モデルが見つかりません: {served_name}")
    return model


@router.get("")
async def list_conversations(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[ConversationOut]:
    result = await db.execute(
        select(Conversation, Model.served_name)
        .outerjoin(Model, Conversation.model_id == Model.id)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
    )
    return [_conversation_out(c, served_name) for c, served_name in result.all()]


@router.post("", status_code=201)
async def create_conversation(
    body: ConversationCreateRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    model = await _resolve_model(db, body.model)

    conversation = Conversation(
        user_id=user.id,
        title=body.title,
        model_id=model.id if model else None,
        system_prompt=body.system_prompt,
        temperature=body.temperature,
        top_p=body.top_p,
        max_tokens=body.max_tokens,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return _conversation_out(conversation, model.served_name if model else None)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    conversation = await _get_owned_conversation(db, user, conversation_id)
    served_name = await _served_name_of(db, conversation.model_id)
    return _conversation_out(conversation, served_name)


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: int,
    body: ConversationUpdateRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    conversation = await _get_owned_conversation(db, user, conversation_id)

    if body.model is not None:
        model = await _resolve_model(db, body.model)
        conversation.model_id = model.id if model else None
    if body.title is not None:
        conversation.title = body.title
    if body.system_prompt is not None:
        conversation.system_prompt = body.system_prompt
    if body.temperature is not None:
        conversation.temperature = body.temperature
    if body.top_p is not None:
        conversation.top_p = body.top_p
    if body.max_tokens is not None:
        conversation.max_tokens = body.max_tokens
    conversation.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(conversation)
    served_name = await _served_name_of(db, conversation.model_id)
    return _conversation_out(conversation, served_name)


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    conversation = await _get_owned_conversation(db, user, conversation_id)
    await db.delete(conversation)
    await db.commit()


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    await _get_owned_conversation(db, user, conversation_id)
    result = await db.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
    )
    return [
        MessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at)
        for m in result.scalars()
    ]


class SendMessageRequest(BaseModel):
    content: str


async def _get_limits(db: AsyncSession, user_id: int) -> tuple[int, int]:
    settings = get_settings()
    result = await db.execute(select(Quota).where(Quota.user_id == user_id))
    quota = result.scalar_one_or_none()
    rpm_limit = quota.rpm_limit if quota else settings.default_rpm_limit
    max_concurrent = quota.max_concurrent if quota else settings.default_max_concurrent
    return rpm_limit, max_concurrent


async def _record_usage(
    *,
    user_id: int,
    api_key_id: int | None,
    model_id: int | None,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    ttft_ms: int | None,
    status: str,
    error_code: str | None,
) -> None:
    """usage_logs への記録はbest-effort。失敗しても推論レスポンスは壊さない。"""
    try:
        async with async_session_maker() as db:
            db.add(
                UsageLog(
                    user_id=user_id,
                    api_key_id=api_key_id,
                    model_id=model_id,
                    app="playground",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency_ms,
                    ttft_ms=ttft_ms,
                    status=status,
                    error_code=error_code,
                )
            )
            await db.commit()
    except Exception:
        logger.warning("usage_log_write_failed", user_id=user_id, status=status)


def _extract_content(chunk: bytes, state: dict[str, Any]) -> None:
    """SSEチャンクから content の断片をベストエフォートで取り込む(usageはrewrite_chunkが担う)。"""
    for line in chunk.decode("utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        try:
            obj = json.loads(line[len("data: ") :])
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if choices:
            content = (choices[0].get("delta") or {}).get("content")
            if content:
                state["content"] = state.get("content", "") + content


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    body: SendMessageRequest,
    ctx: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    user = ctx.user
    conversation = await _get_owned_conversation(db, user, conversation_id)

    if conversation.model_id is None:
        raise InvalidRequestError("この会話にはモデルが設定されていません")
    model = await db.get(Model, conversation.model_id)
    if model is None or model.kind != "chat" or not model.is_enabled:
        raise NotFoundError(f"モデルが見つかりません: {conversation.model_id}")

    result = await db.execute(
        select(ModelPermission).where(
            ModelPermission.model_id == model.id, ModelPermission.role == user.role
        )
    )
    if result.scalar_one_or_none() is None:
        raise PermissionError_(f"モデルの利用権限がありません: {model.served_name}")

    rpm_limit, max_concurrent = await _get_limits(db, user.id)
    rate_limiter.check(user.id, rpm_limit)
    if not concurrency_slots.acquire(user.id, max_concurrent):
        raise RateLimitError("同時実行数の上限に達しました", headers={"Retry-After": "1"})

    # ユーザーメッセージは推論の成否に関わらず先に保存する。
    user_message = Message(conversation_id=conversation.id, role="user", content=body.content)
    db.add(user_message)
    conversation.updated_at = datetime.now(UTC)
    await db.commit()

    history_result = await db.execute(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id)
    )
    backend_messages: list[dict[str, str]] = []
    if conversation.system_prompt:
        backend_messages.append({"role": "system", "content": conversation.system_prompt})
    backend_messages.extend(
        {"role": m.role, "content": m.content} for m in history_result.scalars()
    )

    backend_payload: dict[str, Any] = {
        "model": model.backend_name,
        "messages": backend_messages,
        "temperature": conversation.temperature,
        "top_p": conversation.top_p,
        "stream": True,
    }
    if conversation.max_tokens is not None:
        backend_payload["max_tokens"] = conversation.max_tokens

    backend = get_chat_backend()
    started_at = time.monotonic()
    conversation_id_ = conversation.id
    model_id_ = model.id
    served_name_ = model.served_name
    api_key_id = ctx.api_key_id
    user_id = user.id

    async def _wrapped_stream() -> AsyncIterator[bytes]:
        stream_status = "ok"
        stream_error_code: str | None = None
        first_chunk_at: float | None = None
        state: dict[str, Any] = {}
        try:
            stream_payload = with_stream_usage(backend_payload)
            async for chunk in backend.chat_stream(stream_payload):
                if first_chunk_at is None:
                    first_chunk_at = time.monotonic()
                _extract_content(chunk, state)
                yield rewrite_chunk(chunk, served_name_, state)
        except Exception as exc:
            stream_status = "error"
            stream_error_code = type(exc).__name__
            raise
        finally:
            concurrency_slots.release(user_id)
            content = state.get("content", "")
            if content:
                await asyncio.shield(_save_assistant_message(conversation_id_, content))
            ttft_ms = int((first_chunk_at - started_at) * 1000) if first_chunk_at else None
            await asyncio.shield(
                _record_usage(
                    user_id=user_id,
                    api_key_id=api_key_id,
                    model_id=model_id_,
                    prompt_tokens=state.get("prompt_tokens", 0),
                    completion_tokens=state.get("completion_tokens", 0),
                    latency_ms=int((time.monotonic() - started_at) * 1000),
                    ttft_ms=ttft_ms,
                    status=stream_status,
                    error_code=stream_error_code,
                )
            )

    return StreamingResponse(_wrapped_stream(), media_type="text/event-stream")


async def _save_assistant_message(conversation_id: int, content: str) -> None:
    """アシスタント応答の保存もbest-effort(usage_logsと同様にDB障害時はwarnのみ)。"""
    try:
        async with async_session_maker() as db:
            db.add(Message(conversation_id=conversation_id, role="assistant", content=content))
            await db.execute(
                Conversation.__table__.update()
                .where(Conversation.id == conversation_id)
                .values(updated_at=datetime.now(UTC))
            )
            await db.commit()
    except Exception:
        logger.warning("assistant_message_save_failed", conversation_id=conversation_id)
