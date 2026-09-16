"""RAG検索: 質問を埋め込み化してpgvectorで検索し、引用付きで回答する。

`/api/v1` のOpenAI互換契約とは別の専用エンドポイントとして提供する
(D-016)。文書は全ユーザー共有だが、回答生成に使うモデル自体の
権限チェック(model_permissions)は他のエンドポイントと同様に行う。

埋め込み推論・チャット推論のいずれも `/api/v1` 経由の推論と同様に
レート制限・同時実行スロット・usage_logs記録の対象とする(CLAUDE.md
非交渉事項#3)。ただし処理順序は `/api/v1` (存在確認→権限→レート制限→
スロット)とは異なり、レート制限・スロットをモデル解決より先に行う
(埋め込みモデル解決にもDBアクセスがあるため、権限の無いユーザーからの
リクエストでも先に安価なレート制限で弾けるようにするため)。

回答生成はSSEストリーミングで返す(T-27)。検索(埋め込み+ベクトル検索)は
十分速いため同期的に行い、関連文書が見つからない場合はそのまま従来通り
JSON即時応答(ストリーミングなし)を返す。関連文書があり実際にチャット
推論を行う場合のみ、`text/event-stream`で
`{"type":"status","phase":"generating"}` →
(`{"type":"reasoning","content":...}`(複数回、思考モードを持つモデルの
ときだけ、T-31) →) `{"type":"delta","content":...}`(複数回) →
`{"type":"citations","citations":[...]}` → `data: [DONE]`
の順にイベントを送る。フロントエンド側は質問送信からこのイベント到着
までの間を「検索中」として表示する(検索自体は同期処理なのでサーバー側に
専用の"searching"イベントは存在しない)。
"""

import asyncio
import json
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
from app.deps import AuthContext, current_auth
from app.errors import AppError, NotFoundError, PermissionError_, RateLimitError
from app.limits import concurrency_slots, rate_limiter
from app.models import Chunk, Document, Model, ModelPermission, Quota, UsageLog, User
from app.rag import pick_enabled_model
from app.sse import with_stream_usage

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
    # モデル選択規則(id昇順で最初の有効なモデル)は app.rag.pick_enabled_model に
    # 一本化してある。index_document(app/rag.py)と別実装にすると、埋め込み
    # モデルが検索時とインデックス時で食い違い、ベクトル空間の不一致で
    # 検索結果が静かに壊れる(過去に実際そうなっていた)。
    model = await pick_enabled_model(db, kind)
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
                    app="rag",
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


def _sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def _extract_stream_events(line: str, usage: dict[str, int]) -> list[bytes]:
    """バックエンドの生SSEの1行から、フロントエンド向けのイベントを取り出す。

    `reasoning`(思考内容、T-31)と`content`(本文)は別フィールドで届き、
    同じチャンクに両方入ることは無い(実機で確認済み)が、将来の変化に
    備えて両方チェックし、それぞれ別イベントとして返す(1行から複数
    イベントが出ることはリスト化して表現する)。
    usageが含まれていれば usage dict を更新する(ログ記録専用。フロントには送らない)。
    呼び出し側で、バックエンドのチャンク境界と行境界が一致しない場合に備えて
    行単位にバッファリングしてから渡すこと(1行分の完全な文字列を渡す前提)。
    """
    line = line.strip()
    if not line.startswith("data: ") or line == "data: [DONE]":
        return []
    try:
        obj = json.loads(line[len("data: ") :])
    except json.JSONDecodeError:
        return []
    events: list[bytes] = []
    choices = obj.get("choices") or []
    if choices:
        delta = choices[0].get("delta") or {}
        reasoning = delta.get("reasoning")
        if reasoning:
            events.append(_sse({"type": "reasoning", "content": reasoning}))
        content = delta.get("content")
        if content:
            events.append(_sse({"type": "delta", "content": content}))
    obj_usage = obj.get("usage")
    if obj_usage:
        usage["prompt_tokens"] = obj_usage.get("prompt_tokens", 0)
        usage["completion_tokens"] = obj_usage.get("completion_tokens", 0)
    return events


@router.post("/query", response_model=None)
async def rag_query(
    body: RagQueryRequest,
    ctx: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
) -> RagQueryResponse | StreamingResponse:
    user = ctx.user
    settings = get_settings()
    started_at = time.monotonic()
    embed_model: Model | None = None
    chat_model: Model | None = None
    status_ = "ok"
    error_code: str | None = None
    handed_off_to_stream = False

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
            stream_payload = with_stream_usage(
                {"model": chat_model.backend_name, "messages": messages, "stream": True}
            )
            citations = [
                Citation(
                    document_id=document.id,
                    filename=document.filename,
                    chunk_id=chunk.id,
                    content=chunk.content,
                )
                for chunk, document in relevant
            ]
            chat_model_id = chat_model.id

            async def _wrapped_stream() -> AsyncIterator[bytes]:
                stream_status = "ok"
                stream_error_code: str | None = None
                first_chunk_at: float | None = None
                usage: dict[str, int] = {}
                line_buffer = ""
                try:
                    yield _sse({"type": "status", "phase": "generating"})
                    async for raw_chunk in chat_backend.chat_stream(stream_payload):
                        if first_chunk_at is None:
                            first_chunk_at = time.monotonic()
                        # バックエンドのチャンク境界は`data: ...`の行境界と一致すると
                        # 限らない(1行が2つのチャンクにまたがることがある)。RAGでは
                        # このパーサがフロントへ送るdeltaの唯一の情報源になる
                        # (/api/v1・/api/conversationsのように生バイト列を素通しして
                        # クライアント側で再組み立てさせているわけではない)ため、
                        # 行単位にバッファリングしてから処理する。
                        line_buffer += raw_chunk.decode("utf-8", errors="ignore")
                        lines = line_buffer.split("\n")
                        line_buffer = lines.pop()
                        for line in lines:
                            for event in _extract_stream_events(line, usage):
                                yield event
                    if line_buffer.strip():
                        for event in _extract_stream_events(line_buffer, usage):
                            yield event
                    yield _sse(
                        {
                            "type": "citations",
                            "citations": [c.model_dump() for c in citations],
                        }
                    )
                    yield b"data: [DONE]\n\n"
                except Exception as exc:
                    stream_status, stream_error_code = "error", type(exc).__name__
                    raise
                finally:
                    concurrency_slots.release(user.id)
                    ttft_ms = int((first_chunk_at - started_at) * 1000) if first_chunk_at else None
                    await asyncio.shield(
                        _record_usage(
                            user_id=user.id,
                            api_key_id=ctx.api_key_id,
                            model_id=chat_model_id,
                            prompt_tokens=usage.get("prompt_tokens", 0),
                            completion_tokens=usage.get("completion_tokens", 0),
                            latency_ms=int((time.monotonic() - started_at) * 1000),
                            ttft_ms=ttft_ms,
                            status=stream_status,
                            error_code=stream_error_code,
                        )
                    )

            resp = StreamingResponse(_wrapped_stream(), media_type="text/event-stream")
            handed_off_to_stream = True
            return resp
        finally:
            if not handed_off_to_stream:
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
        # ストリーミングに引き渡した場合は _wrapped_stream 側のfinallyが記録するため
        # ここでは何もしない(二重記録防止)。
        if not handed_off_to_stream:
            await asyncio.shield(
                _record_usage(
                    user_id=user.id,
                    api_key_id=ctx.api_key_id,
                    model_id=chat_model.id
                    if chat_model
                    else (embed_model.id if embed_model else None),
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=int((time.monotonic() - started_at) * 1000),
                    ttft_ms=None,
                    status=status_,
                    error_code=error_code,
                )
            )
