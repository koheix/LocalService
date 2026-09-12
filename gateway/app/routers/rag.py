"""RAG検索: 質問を埋め込み化してpgvectorで検索し、引用付きで回答する。

`/api/v1` のOpenAI互換契約とは別の専用エンドポイントとして提供する
(D-016)。文書は全ユーザー共有だが、回答生成に使うモデル自体の
権限チェック(model_permissions)は他のエンドポイントと同様に行う。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backends import get_chat_backend, get_embed_backend
from app.config import get_settings
from app.db import get_db
from app.deps import current_user
from app.errors import NotFoundError, PermissionError_
from app.models import Chunk, Document, Model, ModelPermission, User

router = APIRouter(prefix="/api/rag", tags=["rag"])

# コサイン距離(0=完全一致 〜 2=正反対)。これより遠い結果は
# 「関係ない質問」とみなしてLLMを呼ばずに済ませる目安値。
_DISTANCE_THRESHOLD = 0.6


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
    result = await db.execute(select(Model).where(Model.kind == kind, Model.is_enabled.is_(True)))
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


@router.post("/query")
async def rag_query(
    body: RagQueryRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> RagQueryResponse:
    settings = get_settings()

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
        if distance is not None and distance <= _DISTANCE_THRESHOLD
    ]

    if not relevant:
        return RagQueryResponse(answer="関連する社内文書が見つかりませんでした。", citations=[])

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
        {"role": "user", "content": f"# コンテキスト\n{context_blocks}\n\n# 質問\n{body.question}"},
    ]
    chat_result = await chat_backend.chat({"model": chat_model.backend_name, "messages": messages})
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
