"""OpenAI 互換エンドポイント。バックエンド固有の語彙を持ち込まないこと。

処理順序(docs/API.md): 認証 → 存在確認 → 権限チェック → レート制限
→ 同時実行スロット取得 → 転送 → usage_logs記録(成功・失敗とも)。

usage_logs への記録は best-effort。記録の失敗が推論レスポンスを
壊してはならない(例外は握ってwarnログのみ)。
"""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backends import get_chat_backend, get_embed_backend
from app.config import get_settings
from app.db import async_session_maker, get_db
from app.deps import AuthContext, current_auth, current_user
from app.errors import AppError, NotFoundError, PermissionError_, RateLimitError
from app.limits import concurrency_slots, rate_limiter
from app.models import Model, ModelPermission, Quota, UsageLog, User
from app.sse import rewrite_chunk, with_stream_usage

router = APIRouter(prefix="/api/v1", tags=["v1"])

logger = structlog.get_logger()


class EmbeddingsRequest(BaseModel):
    model: str
    input: str | list[str]


async def _get_permitted_model(db: AsyncSession, user: User, served_name: str, kind: str) -> Model:
    result = await db.execute(
        select(Model).where(Model.served_name == served_name, Model.is_enabled.is_(True))
    )
    model = result.scalar_one_or_none()
    if model is None or model.kind != kind:
        raise NotFoundError(f"モデルが見つかりません: {served_name}")

    result = await db.execute(
        select(ModelPermission).where(
            ModelPermission.model_id == model.id, ModelPermission.role == user.role
        )
    )
    if result.scalar_one_or_none() is None:
        raise PermissionError_(f"モデルの利用権限がありません: {served_name}")
    return model


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
                    app="api",
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


@router.get("/models")
async def list_models(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    result = await db.execute(select(Model).where(Model.is_enabled.is_(True)))
    return {
        "object": "list",
        "data": [
            {"id": m.served_name, "object": "model", "owned_by": "local"} for m in result.scalars()
        ],
    }


@router.post("/chat/completions")
async def chat_completions(
    payload: dict[str, Any],
    ctx: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> Any:
    user = ctx.user
    served_name = payload.get("model", "")
    started_at = time.monotonic()
    model: Model | None = None
    status_ = "ok"
    error_code: str | None = None
    handed_off_to_stream = False
    usage: dict[str, int] = {}

    try:
        model = await _get_permitted_model(db, user, served_name, "chat")

        rpm_limit, max_concurrent = await _get_limits(db, user.id)
        rate_limiter.check(user.id, rpm_limit)
        if not concurrency_slots.acquire(user.id, max_concurrent):
            raise RateLimitError("同時実行数の上限に達しました", headers={"Retry-After": "1"})

        backend_payload = {**payload, "model": model.backend_name}
        backend = get_chat_backend()
        model_id = model.id

        if payload.get("stream"):
            handed_off_to_stream = True

            async def _wrapped_stream() -> AsyncIterator[bytes]:
                stream_status = "ok"
                stream_error_code: str | None = None
                first_chunk_at: float | None = None
                usage: dict[str, int] = {}
                try:
                    stream_payload = with_stream_usage(backend_payload)
                    async for chunk in backend.chat_stream(stream_payload):
                        if first_chunk_at is None:
                            first_chunk_at = time.monotonic()
                        yield rewrite_chunk(chunk, served_name, usage)
                except Exception as exc:
                    stream_status = "error"
                    stream_error_code = type(exc).__name__
                    raise
                finally:
                    concurrency_slots.release(user.id)
                    ttft_ms = int((first_chunk_at - started_at) * 1000) if first_chunk_at else None
                    # クライアント切断でこのタスク自体がキャンセルされていても
                    # ログ書き込みは完了させる(shieldしないとCancelledErrorで
                    # 記録が失われる)。
                    await asyncio.shield(
                        _record_usage(
                            user_id=user.id,
                            api_key_id=ctx.api_key_id,
                            model_id=model_id,
                            prompt_tokens=usage.get("prompt_tokens", 0),
                            completion_tokens=usage.get("completion_tokens", 0),
                            latency_ms=int((time.monotonic() - started_at) * 1000),
                            ttft_ms=ttft_ms,
                            status=stream_status,
                            error_code=stream_error_code,
                        )
                    )

            return StreamingResponse(_wrapped_stream(), media_type="text/event-stream")

        try:
            result = await backend.chat(backend_payload)
            usage = result.get("usage") or {}
            result["model"] = served_name  # 内部実体名(backend_name)を漏らさない(D-003)
            return result
        finally:
            concurrency_slots.release(user.id)

    except RateLimitError as exc:
        status_, error_code = "rate_limited", exc.code
        raise
    except AppError as exc:
        status_, error_code = "error", exc.code
        raise
    except Exception as exc:
        status_, error_code = "error", type(exc).__name__
        raise
    finally:
        if not handed_off_to_stream:
            await asyncio.shield(
                _record_usage(
                    user_id=user.id,
                    api_key_id=ctx.api_key_id,
                    model_id=model.id if model else None,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    latency_ms=int((time.monotonic() - started_at) * 1000),
                    ttft_ms=None,
                    status=status_,
                    error_code=error_code,
                )
            )


@router.post("/embeddings")
async def embeddings(
    body: EmbeddingsRequest,
    ctx: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = ctx.user
    started_at = time.monotonic()
    model: Model | None = None
    status_ = "ok"
    error_code: str | None = None

    try:
        model = await _get_permitted_model(db, user, body.model, "embedding")

        rpm_limit, max_concurrent = await _get_limits(db, user.id)
        rate_limiter.check(user.id, rpm_limit)
        if not concurrency_slots.acquire(user.id, max_concurrent):
            raise RateLimitError("同時実行数の上限に達しました", headers={"Retry-After": "1"})

        try:
            texts = [body.input] if isinstance(body.input, str) else body.input
            backend = get_embed_backend()
            vectors = await backend.embed(texts, model.backend_name)
            return {
                "object": "list",
                "data": [
                    {"object": "embedding", "index": i, "embedding": vec}
                    for i, vec in enumerate(vectors)
                ],
                "model": body.model,
            }
        finally:
            concurrency_slots.release(user.id)

    except RateLimitError as exc:
        status_, error_code = "rate_limited", exc.code
        raise
    except AppError as exc:
        status_, error_code = "error", exc.code
        raise
    except Exception as exc:
        status_, error_code = "error", type(exc).__name__
        raise
    finally:
        await asyncio.shield(
            _record_usage(
                user_id=user.id,
                api_key_id=ctx.api_key_id,
                model_id=model.id if model else None,
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=int((time.monotonic() - started_at) * 1000),
                ttft_ms=None,
                status=status_,
                error_code=error_code,
            )
        )
