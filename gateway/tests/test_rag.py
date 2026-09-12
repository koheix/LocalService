"""RAG検索(/api/rag/query)のテスト。バックエンドは FakeBackend。

`_pick_permitted_model`は「kind一致・有効な最初のモデル」を選ぶため、
共有DBに他のテストや seed(chat-standard/embed-standard)が作った
同種のモデルが残っていると、どれが選ばれるか不定になる。
`isolate_model` フィクスチャで、テスト対象のモデル以外の同kindモデルを
一時的に無効化し、テスト終了時に必ず元の状態へ戻す
(seedデータを壊したまま次のテスト/手動確認に影響させないため)。
"""

from collections.abc import Awaitable, Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document, Model
from tests.conftest import FakeBackend


def _vec(value: float, dim: int = 1024) -> list[float]:
    v = [0.0] * dim
    v[0] = value
    return v


class ControlledEmbedBackend(FakeBackend):
    """埋め込み結果を固定ベクトルに差し替えて、コサイン距離を制御可能にする。"""

    def __init__(self, vector: list[float]) -> None:
        super().__init__()
        self._vector = vector

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        self.embed_calls.append((texts, model))
        return [self._vector for _ in texts]


@pytest_asyncio.fixture
async def isolate_model(db: AsyncSession) -> Callable[[Model], Awaitable[None]]:
    disabled_ids: list[int] = []

    async def _do(model: Model) -> None:
        result = await db.execute(
            select(Model.id).where(
                Model.kind == model.kind, Model.id != model.id, Model.is_enabled.is_(True)
            )
        )
        ids = [row[0] for row in result.all()]
        if ids:
            await db.execute(update(Model).where(Model.id.in_(ids)).values(is_enabled=False))
            await db.commit()
            disabled_ids.extend(ids)

    yield _do

    if disabled_ids:
        await db.execute(update(Model).where(Model.id.in_(disabled_ids)).values(is_enabled=True))
        await db.commit()


async def _add_chunk(
    db: AsyncSession, owner_id: int, embedding: list[float]
) -> tuple[Document, Chunk]:
    document = Document(
        owner_id=owner_id,
        filename="doc.txt",
        mime_type="text/plain",
        size_bytes=10,
        storage_path="/tmp/doc-for-rag-test.txt",
        status="ready",
    )
    db.add(document)
    await db.flush()
    chunk = Chunk(
        document_id=document.id, ordinal=0, content="GTX 1080のVRAMは8GBです", embedding=embedding
    )
    db.add(chunk)
    await db.commit()
    await db.refresh(document)
    await db.refresh(chunk)
    return document, chunk


async def test_rag_query_requires_auth(client: AsyncClient) -> None:
    resp = await client.post("/api/rag/query", json={"question": "hi"})
    assert resp.status_code == 401


async def test_rag_query_no_relevant_chunks_skips_llm(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    await isolate_model(embed_model)

    fake_embed = ControlledEmbedBackend(_vec(1.0))
    monkeypatch.setattr("app.routers.rag.get_embed_backend", lambda: fake_embed)

    resp = await client.post("/api/rag/query", json={"question": "関係ない質問"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["citations"] == []
    assert "見つかりません" in body["answer"]


async def test_rag_query_returns_citation_for_relevant_chunk(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    chat_model = await make_model(kind="chat")
    await isolate_model(embed_model)
    await isolate_model(chat_model)

    document, chunk = await _add_chunk(db, user.id, _vec(1.0))

    # 質問の埋め込みをチャンクと同一ベクトルにしてコサイン距離0にする。
    fake_embed = ControlledEmbedBackend(_vec(1.0))
    monkeypatch.setattr("app.routers.rag.get_embed_backend", lambda: fake_embed)
    monkeypatch.setattr("app.routers.rag.get_chat_backend", FakeBackend)

    resp = await client.post("/api/rag/query", json={"question": "GTX 1080のVRAMは？"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "fake reply"
    assert len(body["citations"]) == 1
    assert body["citations"][0]["document_id"] == document.id
    assert body["citations"][0]["chunk_id"] == chunk.id
    assert body["citations"][0]["filename"] == document.filename


async def test_rag_query_permission_denied_for_chat_model(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await login_as_new_user(role="user")
    embed_model = await make_model(kind="embedding")
    chat_model = await make_model(kind="chat", roles=("admin",))  # user ロールには権限なし
    await isolate_model(embed_model)
    await isolate_model(chat_model)

    await _add_chunk(db, user.id, _vec(1.0))

    fake_embed = ControlledEmbedBackend(_vec(1.0))
    monkeypatch.setattr("app.routers.rag.get_embed_backend", lambda: fake_embed)

    resp = await client.post("/api/rag/query", json={"question": "GTX 1080のVRAMは？"})
    assert resp.status_code == 403
