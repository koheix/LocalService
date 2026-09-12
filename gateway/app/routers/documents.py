"""文書アップロード(RAG)。全ユーザー共有の社内ナレッジベース(D-016)。

`owner_id` はアップロードした人の記録用のみで、閲覧・検索の
アクセス制御には使わない(誰がアップロードしても全員が検索できる)。
"""

import asyncio
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.deps import current_user
from app.errors import InvalidRequestError, NotFoundError, PermissionError_
from app.models import Document, User
from app.rag import index_document

router = APIRouter(prefix="/api/documents", tags=["documents"])

_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
_UPLOAD_READ_CHUNK_SIZE = 1024 * 1024  # 1MB


async def _read_upload_within_limit(file: UploadFile, max_size: int) -> bytes:
    """上限を超えた時点で打ち切り、上限超のファイル全体をメモリに載せない。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise InvalidRequestError(f"ファイルサイズが上限({max_size}バイト)を超えています")
        chunks.append(chunk)
    return b"".join(chunks)


class DocumentOut(BaseModel):
    id: int
    owner_id: int
    filename: str
    mime_type: str
    size_bytes: int
    status: str
    created_at: datetime


def _document_out(d: Document) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        owner_id=d.owner_id,
        filename=d.filename,
        mime_type=d.mime_type,
        size_bytes=d.size_bytes,
        status=d.status,
        created_at=d.created_at,
    )


@router.get("")
async def list_documents(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[DocumentOut]:
    result = await db.execute(select(Document).order_by(Document.created_at.desc()))
    return [_document_out(d) for d in result.scalars()]


@router.post("", status_code=201)
async def upload_document(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    settings = get_settings()
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise InvalidRequestError(
            f"対応していないファイル形式です(pdf/docx/txtのみ): {ext or '(拡張子なし)'}"
        )

    data = await _read_upload_within_limit(file, settings.max_upload_size_bytes)

    uploads_dir = Path(settings.uploads_dir)
    storage_path = uploads_dir / f"{uuid.uuid4().hex}{ext}"

    def _write() -> None:
        uploads_dir.mkdir(parents=True, exist_ok=True)
        storage_path.write_bytes(data)

    await asyncio.to_thread(_write)

    document = Document(
        owner_id=user.id,
        filename=file.filename or storage_path.name,
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=len(data),
        storage_path=str(storage_path),
        status="pending",
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    background_tasks.add_task(index_document, document.id)
    return _document_out(document)


@router.get("/{document_id}")
async def get_document(
    document_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    document = await db.get(Document, document_id)
    if document is None:
        raise NotFoundError(f"文書が見つかりません: {document_id}")
    return _document_out(document)


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    document = await db.get(Document, document_id)
    if document is None:
        raise NotFoundError(f"文書が見つかりません: {document_id}")
    if document.owner_id != user.id and user.role != "admin":
        raise PermissionError_("この文書を削除する権限がありません")

    storage_path = Path(document.storage_path)
    await db.delete(document)
    await db.commit()
    await asyncio.to_thread(storage_path.unlink, missing_ok=True)
