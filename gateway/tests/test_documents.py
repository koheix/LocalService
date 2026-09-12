"""文書アップロードAPI(/api/documents)のテスト。

BackgroundTasksによるインデックス化(app.rag.index_document)は
別ファイル(test_rag_indexing.py 相当)ではなく、ここではトリガーの
配線が壊れていないことを最低限確認する程度に留め、抽出ロジック自体は
monkeypatchで無害化する(実ファイルシステム上のI/Oを伴うため)。
"""

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document


@pytest.fixture(autouse=True)
def _skip_indexing(monkeypatch: pytest.MonkeyPatch) -> None:
    """index_document をノーオペにする(chunkingや埋め込みはT-16/T-17側で検証済み)。"""

    async def _noop(document_id: int) -> None:
        return None

    monkeypatch.setattr("app.routers.documents.index_document", _noop)


async def test_upload_requires_auth(client: AsyncClient) -> None:
    resp = await client.post("/api/documents", files={"file": ("a.txt", b"hello", "text/plain")})
    assert resp.status_code == 401


async def test_upload_rejects_unsupported_extension(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    await login_as_new_user()
    resp = await client.post(
        "/api/documents", files={"file": ("a.exe", b"hello", "application/octet-stream")}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"


async def test_upload_and_shared_list(
    client: AsyncClient, login_as_new_user: Callable, db: AsyncSession
) -> None:
    await login_as_new_user()
    created = await client.post(
        "/api/documents", files={"file": ("note.txt", b"hello world", "text/plain")}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["filename"] == "note.txt"
    assert body["status"] == "pending"

    # 別ユーザーからも一覧に見える(全ユーザー共有)。
    await login_as_new_user()
    listed = await client.get("/api/documents")
    assert any(d["id"] == body["id"] for d in listed.json())

    document = await db.get(Document, body["id"])
    assert document is not None
    stored_bytes = await asyncio.to_thread(Path(document.storage_path).read_bytes)
    assert stored_bytes == b"hello world"


async def test_delete_forbidden_for_non_owner(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    await login_as_new_user()
    created = await client.post(
        "/api/documents", files={"file": ("note.txt", b"hello", "text/plain")}
    )
    doc_id = created.json()["id"]

    await login_as_new_user()  # 別ユーザー
    resp = await client.delete(f"/api/documents/{doc_id}")
    assert resp.status_code == 403


async def test_owner_delete_removes_row_and_file(
    client: AsyncClient, login_as_new_user: Callable, db: AsyncSession
) -> None:
    await login_as_new_user()
    created = await client.post(
        "/api/documents", files={"file": ("note.txt", b"hello", "text/plain")}
    )
    doc_id = created.json()["id"]
    document = await db.get(Document, doc_id)
    storage_path = Path(document.storage_path)
    assert await asyncio.to_thread(storage_path.exists)

    resp = await client.delete(f"/api/documents/{doc_id}")
    assert resp.status_code == 204

    result = await db.execute(select(Document).where(Document.id == doc_id))
    assert result.scalar_one_or_none() is None
    assert not await asyncio.to_thread(storage_path.exists)


async def test_get_unknown_document_404(client: AsyncClient, login_as_new_user: Callable) -> None:
    await login_as_new_user()
    resp = await client.get("/api/documents/999999999")
    assert resp.status_code == 404
