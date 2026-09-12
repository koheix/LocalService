"""RAG検索: 質問を埋め込み化してpgvectorで検索し、引用付きで回答する。

`/api/v1` のOpenAI互換契約とは別の専用エンドポイントとして提供する
(D-016)。文書は全ユーザー共有だが、回答生成に使うモデル自体の
権限チェック(model_permissions)は他のエンドポイントと同様に行う。

埋め込み推論・チャット推論のいずれも `/api/v1` 経由の推論と同様に
レート制限・同時実行スロット・usage_logs記録の対象とする(CLAUDE.md
非交渉事項#3)。
"""

import asyncio
import time

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backends import get_chat_backend, get_embed_backend
from app.config import get_settings
from app.db import async_session_maker, get_db
from app.deps import AuthContext, current_auth
from app.errors import AppError, NotFoundError, PermissionError_, RateLimitError
from app.limits import concurrency_slots, rate_limiter
from app.models import Chunk, Document, Model, ModelPermission, Quota, UsageLog, User

router = APIRouter(prefix="/api/rag", tags=["rag"])

logger = structlog.get_logger()


class RagQueryRequest(BaseModel):
    question: str


class Citation(BaseModel):
    document_id: int
    filename: str
    chunk_id: int
    content: str


class RagQueryResponse(BaseModel):
    answer: str
    citations: list[Citation]


async def _pick_permitted_model(db: AsyncSession, user: User, kind: str) -> Model:
    # ORDER BY が無いと有効なモデルが複数ある場合に選ばれるモデルが
    # 不定になり、インデックス時と検索時で埋め込みモデルが食い違って
    # 検索結果が静かに壊れうる。id 昇順で固定する。
    result = await db.execute(
        select(Model).where(Model.kind == kind, Model.is_enabled.is_(True)).order_by(Model.id)
    )
    model = result.scalars().first()
    if model is None:
        raise NotFoundError(f"利用可能な{kind}モデルがありません")

    perm = await db.execute(
        select(ModelPermission).where(
            ModelPermission.model_id == model.id, ModelPermission.role == user.role
        )
    )
    if perm.scalar_one_or_none() is None:
        raise PermissionError_(f"モデルの利用権限がありません: {model.served_name}")
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
                    app="rag",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency_ms,
                    ttft_ms=None,
                    status=status,
                    error_code=error_code,
                )
            )
            await db.commit()
    except Exception:
        logger.warning("usage_log_write_failed", user_id=user_id, status=status)


@router.post("/query")
async def rag_query(
    body: RagQueryRequest,
    ctx: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> RagQueryResponse:
    user = ctx.user
    settings = get_settings()
    started_at = time.monotonic()
    embed_model: Model | None = None
    chat_model: Model | None = None
    chat_usage: dict[str, int] = {}
    status_ = "ok"
    error_code: str | None = None

    try:
        rpm_limit, max_concurrent = await _get_limits(db, user.id)
        rate_limiter.check(user.id, rpm_limit)
        if not concurrency_slots.acquire(user.id, max_concurrent):
            raise RateLimitError("同時実行数の上限に達しました", headers={"Retry-After": "1"})

        try:
            embed_model = await _pick_permitted_model(db, user, "embedding")
            embed_backend = get_embed_backend()
            [query_vector] = await embed_backend.embed([body.question], embed_model.backend_name)

            distance_expr = Chunk.embedding.cosine_distance(query_vector)
            result = await db.execute(
                select(Chunk, Document, distance_expr.label("distance"))
                .join(Document, Chunk.document_id == Document.id)
                .order_by(distance_expr)
                .limit(settings.rag_top_k)
            )
            relevant = [
                (chunk, document)
                for chunk, document, distance in result.all()
                if distance is not None and distance <= settings.rag_distance_threshold
            ]

            if not relevant:
                return RagQueryResponse(
                    answer="関連する社内文書が見つかりませんでした。", citations=[]
                )

            chat_model = await _pick_permitted_model(db, user, "chat")
            chat_backend = get_chat_backend()

            context_blocks = "\n\n".join(
                f"[出典{i + 1}: {document.filename}]\n{chunk.content}"
                for i, (chunk, document) in enumerate(relevant)
            )
            messages = [
                {
                    "role": "system",
                    "content": (
                        "あなたは社内文書検索アシスタントです。"
                        "以下のコンテキストの範囲内で質問に日本語で簡潔に答えてください。"
                        "コンテキストに答えがなければ「わかりません」と答えてください。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"# コンテキスト\n{context_blocks}\n\n# 質問\n{body.question}",
                },
            ]
            chat_result = await chat_backend.chat(
                {"model": chat_model.backend_name, "messages": messages}
            )
            chat_usage = chat_result.get("usage") or {}
            answer = chat_result["choices"][0]["message"]["content"]

            return RagQueryResponse(
                answer=answer,
                citations=[
                    Citation(
                        document_id=document.id,
                        filename=document.filename,
                        chunk_id=chunk.id,
                        content=chunk.content,
                    )
                    for chunk, document in relevant
                ],
            )
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
        # チャット推論まで到達していれば chat_model 分として、
        # 「関係ない質問」で埋め込みのみ行った場合は embed_model 分として記録する。
        await asyncio.shield(
            _record_usage(
                user_id=user.id,
                api_key_id=ctx.api_key_id,
                model_id=chat_model.id if chat_model else (embed_model.id if embed_model else None),
                prompt_tokens=chat_usage.get("prompt_tokens", 0),
                completion_tokens=chat_usage.get("completion_tokens", 0),
                latency_ms=int((time.monotonic() - started_at) * 1000),
                status=status_,
                error_code=error_code,
            )
        )
