"""文書アップロードAPI(/api/documents)のテスト。

BackgroundTasksによるインデックス化(app.rag.index_document)の中身
(抽出・分割・埋め込み)は tests/test_rag_indexing.py で検証する。ここでは
- アップロード時に正しい document_id で確実に配線されていること
- 認証・所有権・拡張子・サイズ上限などAPI層の振る舞い
を検証する。
"""

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Chunk, Document


@pytest.fixture(autouse=True)
def _skip_indexing(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """index_document をノーオペにする(中身はtest_rag_indexing.pyで検証済み)。

    呼び出された document_id を記録し、テスト側で「配線が生きているか」を
    検証できるようにする(確認済み: documents.py の
    background_tasks.add_task(index_document, ...) を削除すると、
    calls が空のままになり test_upload_triggers_indexing が失敗する)。
    """
    calls: list[int] = []

    async def _noop(document_id: int) -> None:
        calls.append(document_id)

    monkeypatch.setattr("app.routers.documents.index_document", _noop)
    return calls


async def test_upload_requires_auth(client: AsyncClient) -> None:
    resp = await client.post("/api/documents", files={"file": ("a.txt", b"hello", "text/plain")})
    assert resp.status_code == 401


async def test_list_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/documents")
    assert resp.status_code == 401


async def test_get_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/documents/1")
    assert resp.status_code == 401


async def test_delete_requires_auth(client: AsyncClient) -> None:
    resp = await client.delete("/api/documents/1")
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


async def test_upload_rejects_oversized_file(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    await login_as_new_user()
    settings = get_settings()
    oversized = b"x" * (settings.max_upload_size_bytes + 1)

    resp = await client.post(
        "/api/documents", files={"file": ("pytest-huge.txt", oversized, "text/plain")}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"

    listed = await client.get("/api/documents")
    assert all(d["filename"] != "pytest-huge.txt" for d in listed.json())


async def test_upload_triggers_indexing_with_correct_document_id(
    client: AsyncClient, login_as_new_user: Callable, _skip_indexing: list[int]
) -> None:
    await login_as_new_user()
    created = await client.post(
        "/api/documents", files={"file": ("pytest-note.txt", b"hello world", "text/plain")}
    )
    assert created.status_code == 201
    doc_id = created.json()["id"]
    assert _skip_indexing == [doc_id]


async def test_upload_and_shared_list(
    client: AsyncClient, login_as_new_user: Callable, db: AsyncSession
) -> None:
    await login_as_new_user()
    created = await client.post(
        "/api/documents", files={"file": ("pytest-note.txt", b"hello world", "text/plain")}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["filename"] == "pytest-note.txt"
    assert body["status"] == "pending"

    # 別ユーザーからも一覧に見える(全ユーザー共有)。
    await login_as_new_user()
    listed = await client.get("/api/documents")
    assert any(d["id"] == body["id"] for d in listed.json())

    document = await db.get(Document, body["id"])
    assert document is not None
    stored_bytes = await asyncio.to_thread(Path(document.storage_path).read_bytes)
    assert stored_bytes == b"hello world"


async def test_upload_storage_path_ignores_client_filename(
    client: AsyncClient, login_as_new_user: Callable, db: AsyncSession
) -> None:
    """悪意あるファイル名(パストラバーサル)がstorage_pathの生成に使われないこと。"""
    await login_as_new_user()
    created = await client.post(
        "/api/documents",
        files={"file": ("pytest-../../../../etc/passwd.txt", b"hello", "text/plain")},
    )
    assert created.status_code == 201
    document = await db.get(Document, created.json()["id"])
    assert ".." not in document.storage_path
    assert "etc/passwd" not in document.storage_path
    settings = get_settings()
    assert Path(document.storage_path).parent == Path(settings.uploads_dir)


async def test_delete_forbidden_for_non_owner(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    await login_as_new_user()
    created = await client.post(
        "/api/documents", files={"file": ("pytest-note.txt", b"hello", "text/plain")}
    )
    doc_id = created.json()["id"]

    await login_as_new_user()  # 別ユーザー
    resp = await client.delete(f"/api/documents/{doc_id}")
    assert resp.status_code == 403


async def test_admin_can_delete_others_document(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    await login_as_new_user(role="user")
    created = await client.post(
        "/api/documents", files={"file": ("pytest-note.txt", b"hello", "text/plain")}
    )
    doc_id = created.json()["id"]

    await login_as_new_user(role="admin")
    resp = await client.delete(f"/api/documents/{doc_id}")
    assert resp.status_code == 204

    resp2 = await client.get(f"/api/documents/{doc_id}")
    assert resp2.status_code == 404


async def test_owner_delete_removes_row_and_file(
    client: AsyncClient, login_as_new_user: Callable, db: AsyncSession
) -> None:
    await login_as_new_user()
    created = await client.post(
        "/api/documents", files={"file": ("pytest-note.txt", b"hello", "text/plain")}
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


async def test_owner_delete_cascades_to_chunks(
    client: AsyncClient, login_as_new_user: Callable, db: AsyncSession
) -> None:
    await login_as_new_user()
    created = await client.post(
        "/api/documents", files={"file": ("pytest-note.txt", b"hello", "text/plain")}
    )
    doc_id = created.json()["id"]

    document = await db.get(Document, doc_id)
    db.add(Chunk(document_id=document.id, ordinal=0, content="hello", embedding=[0.0] * 1024))
    await db.commit()

    resp = await client.delete(f"/api/documents/{doc_id}")
    assert resp.status_code == 204

    result = await db.execute(select(Chunk).where(Chunk.document_id == doc_id))
    assert result.scalars().all() == []


async def test_get_unknown_document_404(client: AsyncClient, login_as_new_user: Callable) -> None:
    await login_as_new_user()
    resp = await client.get("/api/documents/999999999")
    assert resp.status_code == 404
