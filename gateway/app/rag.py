"""RAG: 文書のテキスト抽出・チャンク分割・埋め込み生成。

バックグラウンドジョブは Redis/Celery を使わず FastAPI BackgroundTasks
で実行する(D-016)。gateway プロセスが再起動すると進行中のジョブは
失われるが、同時利用1名の検証機では許容する。

推論バックエンドへのアクセスは app.backends 経由のみ行い、Ollama の
語彙はここに持ち込まない。
"""

import asyncio
from pathlib import Path

import structlog
from docx import Document as DocxDocument
from pypdf import PdfReader
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backends import get_embed_backend
from app.config import get_settings
from app.db import async_session_maker
from app.models import Chunk, Document, Model

logger = structlog.get_logger()


async def pick_enabled_model(db: AsyncSession, kind: str) -> Model | None:
    """kind一致・有効なモデルをid昇順で1件選ぶ。

    インデックス時(index_document)と検索時(routers/rag.py)の両方が
    この関数だけを経由する。選択規則を2箇所に別々に実装すると、片方だけ
    変更されたときに埋め込みモデルが食い違い、ベクトル空間の不一致で
    検索結果が静かに壊れる(実際に過去そうなっていた)。
    """
    result = await db.execute(
        select(Model).where(Model.kind == kind, Model.is_enabled.is_(True)).order_by(Model.id)
    )
    return result.scalars().first()


def _extract_text(path: Path, ext: str) -> str:
    if ext == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")
    if ext == ".pdf":
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if ext == ".docx":
        doc = DocxDocument(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    raise ValueError(f"未対応の拡張子です: {ext}")


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    step = max(1, chunk_size - overlap)
    chunks: list[str] = []
    start = 0
    while start < len(text):
        piece = text[start : start + chunk_size].strip()
        if piece:
            chunks.append(piece)
        if start + chunk_size >= len(text):
            break
        start += step
    return chunks


async def index_document(document_id: int) -> None:
    """アップロード後にバックグラウンドで実行する: 抽出→分割→埋め込み→保存。

    失敗しても例外を外に投げない(呼び出し元はBackgroundTasksであり、
    ここで捕まえないとエラーが握りつぶされて documents.status が
    pending のまま残ってしまう)。
    """
    settings = get_settings()
    async with async_session_maker() as db:
        document = await db.get(Document, document_id)
        if document is None:
            return
        document.status = "indexing"
        await db.commit()

        try:
            path = Path(document.storage_path)
            ext = path.suffix.lower()
            text = await asyncio.to_thread(_extract_text, path, ext)
            chunks = _chunk_text(text, settings.chunk_size_chars, settings.chunk_overlap_chars)
            if not chunks:
                raise ValueError("文書からテキストを抽出できませんでした")

            embed_model = await pick_enabled_model(db, "embedding")
            if embed_model is None:
                raise ValueError("埋め込み用モデルが設定されていません")

            backend = get_embed_backend()
            embeddings = await backend.embed(chunks, embed_model.backend_name)

            await db.execute(delete(Chunk).where(Chunk.document_id == document.id))
            for ordinal, (content, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
                db.add(
                    Chunk(
                        document_id=document.id,
                        ordinal=ordinal,
                        content=content,
                        embedding=embedding,
                    )
                )

            document.status = "ready"
            await db.commit()
        except Exception:
            logger.warning("document_indexing_failed", document_id=document_id, exc_info=True)
            try:
                await db.rollback()
                document.status = "failed"
                await db.commit()
            except Exception:
                logger.warning(
                    "document_indexing_failed_status_update_failed", document_id=document_id
                )
