"""OpenAI 互換エンドポイント。バックエンド固有の語彙を持ち込まないこと。

処理順序(docs/API.md): 認証 → 存在確認 → 権限チェック → 転送。
レート制限・同時実行スロット取得・usage_logs記録は T-08 で追加する。
"""

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backends import get_chat_backend, get_embed_backend
from app.db import get_db
from app.deps import current_user
from app.errors import NotFoundError, PermissionError_
from app.models import Model, ModelPermission, User

router = APIRouter(prefix="/api/v1", tags=["v1"])


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
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    served_name = payload.get("model", "")
    model = await _get_permitted_model(db, user, served_name, "chat")
    backend_payload = {**payload, "model": model.backend_name}
    backend = get_chat_backend()

    if payload.get("stream"):
        return StreamingResponse(
            backend.chat_stream(backend_payload), media_type="text/event-stream"
        )
    return await backend.chat(backend_payload)


@router.post("/embeddings")
async def embeddings(
    body: EmbeddingsRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    model = await _get_permitted_model(db, user, body.model, "embedding")
    texts = [body.input] if isinstance(body.input, str) else body.input
    backend = get_embed_backend()
    vectors = await backend.embed(texts, model.backend_name)
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": vec} for i, vec in enumerate(vectors)
        ],
        "model": body.model,
    }
