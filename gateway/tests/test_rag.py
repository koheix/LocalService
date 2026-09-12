"""RAG検索(/api/rag/query)のテスト。バックエンドは FakeBackend。

`_pick_permitted_model` は id昇順で決定的にモデルを選ぶ(routers/rag.py、
app/rag.py とも同じ規則)。ただし共有DBに他のテストやseed
(chat-standard/embed-standard)が作った同種の有効なモデルが残っていると、
そちらの方がid が小さく先に選ばれてしまい、テスト対象のモデルが使われない。
`isolate_model` フィクスチャで、テスト対象のモデル以外の同kindモデルを
一時的に無効化し、テスト終了時に必ず元の状態へ戻す
(seedデータを壊したまま次のテスト/手動確認に影響させないため)。
"""

import asyncio
from collections.abc import Awaitable, Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document, Model, UsageLog
from tests.conftest import FakeBackend


def _vec(value: float, *, index: int = 0, dim: int = 1024) -> list[float]:
    v = [0.0] * dim
    v[index] = value
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
    db: AsyncSession,
    owner_id: int,
    embedding: list[float],
    *,
    content: str = "GTX 1080のVRAMは8GBです",
) -> tuple[Document, Chunk]:
    document = Document(
        owner_id=owner_id,
        filename="pytest-rag-doc.txt",
        mime_type="text/plain",
        size_bytes=10,
        storage_path="/tmp/pytest-doc-for-rag-test.txt",
        status="ready",
    )
    db.add(document)
    await db.flush()
    chunk = Chunk(document_id=document.id, ordinal=0, content=content, embedding=embedding)
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
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """実際に閾値外(直交=距離1.0 > 0.6)のチャンクを1件投入したうえで、
    citations が空になり、かつチャット推論が一度も呼ばれないことを確認する。

    確認済み: routers/rag.py の距離閾値判定を無効化(settings.rag_distance_threshold
    を極端に大きくする)と、本テストのみで fake_chat.chat_calls が空でなくなり失敗する。
    """
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    chat_model = await make_model(kind="chat")
    await isolate_model(embed_model)
    await isolate_model(chat_model)

    # クエリベクトル(index0)と直交するベクトル(index1)のチャンクを投入する。
    await _add_chunk(db, user.id, _vec(1.0, index=1))

    fake_embed = ControlledEmbedBackend(_vec(1.0, index=0))
    fake_chat = FakeBackend()
    monkeypatch.setattr("app.routers.rag.get_embed_backend", lambda: fake_embed)
    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: fake_chat)

    resp = await client.post("/api/rag/query", json={"question": "関係ない質問"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["citations"] == []
    assert "見つかりません" in body["answer"]
    assert fake_chat.chat_calls == []


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
    fake_chat = FakeBackend()
    monkeypatch.setattr("app.routers.rag.get_embed_backend", lambda: fake_embed)
    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: fake_chat)

    resp = await client.post("/api/rag/query", json={"question": "GTX 1080のVRAMは？"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "fake reply"
    citation_ids = [c["chunk_id"] for c in body["citations"]]
    assert chunk.id in citation_ids
    match = next(c for c in body["citations"] if c["chunk_id"] == chunk.id)
    assert match["document_id"] == document.id
    assert match["filename"] == document.filename

    # バックエンドに渡った実際のpayloadを検証する(served_nameではなくbackend_nameで送る, D-003)。
    assert len(fake_chat.chat_calls) == 1
    sent = fake_chat.chat_calls[0]
    assert sent["model"] == chat_model.backend_name
    assert chunk.content in sent["messages"][1]["content"]
    assert "GTX 1080のVRAMは？" in sent["messages"][1]["content"]


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


# ---------------------------------------------------------------------------
# レート制限・同時実行制限・usage_logs記録(CLAUDE.md非交渉事項#3)
# ---------------------------------------------------------------------------


async def test_rag_query_rate_limited_429(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    make_quota: Callable,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    await isolate_model(embed_model)
    await make_quota(user.id, rpm_limit=1)

    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )

    payload = {"question": "hi"}
    r1 = await client.post("/api/rag/query", json=payload)
    r2 = await client.post("/api/rag/query", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 429
    assert r2.json()["error"]["code"] == "RATE_LIMIT"
    assert "Retry-After" in r2.headers


async def test_rag_query_concurrency_limited_and_released(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    make_quota: Callable,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    await isolate_model(embed_model)
    await make_quota(user.id, max_concurrent=1)

    release = asyncio.Event()
    started = asyncio.Event()

    class BlockingEmbedBackend(FakeBackend):
        async def embed(self, texts: list[str], model: str) -> list[list[float]]:
            self.embed_calls.append((texts, model))
            started.set()
            await release.wait()
            return [_vec(1.0) for _ in texts]

    blocking = BlockingEmbedBackend()
    monkeypatch.setattr("app.routers.rag.get_embed_backend", lambda: blocking)

    payload = {"question": "hi"}
    task = asyncio.create_task(client.post("/api/rag/query", json=payload))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        resp2 = await asyncio.wait_for(client.post("/api/rag/query", json=payload), timeout=5)
        assert resp2.status_code == 429
        assert resp2.json()["error"]["code"] == "RATE_LIMIT"
    finally:
        release.set()
        r1 = await asyncio.wait_for(task, timeout=5)
    assert r1.status_code == 200

    # スロットが解放された後は3本目が成功する(concurrency_slots.release()の検証)
    resp3 = await asyncio.wait_for(client.post("/api/rag/query", json=payload), timeout=5)
    assert resp3.status_code == 200


async def test_rag_query_usage_log_when_no_relevant_chunks_records_embed_model(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    await isolate_model(embed_model)

    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )

    resp = await client.post("/api/rag/query", json={"question": "hi"})
    assert resp.status_code == 200

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "rag")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "ok"
    assert logs[0].model_id == embed_model.id


async def test_rag_query_usage_log_on_success_records_chat_model(
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

    await _add_chunk(db, user.id, _vec(1.0))
    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )
    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: FakeBackend())

    resp = await client.post("/api/rag/query", json={"question": "GTX 1080のVRAMは？"})
    assert resp.status_code == 200

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "rag")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "ok"
    assert logs[0].model_id == chat_model.id
    assert logs[0].completion_tokens == 3  # FakeBackend.chat() の固定usage


async def test_rag_query_usage_log_on_rate_limited(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    make_quota: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    await isolate_model(embed_model)
    await make_quota(user.id, rpm_limit=1)

    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )

    payload = {"question": "hi"}
    await client.post("/api/rag/query", json=payload)
    resp2 = await client.post("/api/rag/query", json=payload)
    assert resp2.status_code == 429

    result = await db.execute(select(UsageLog).where(UsageLog.user_id == user.id))
    logs = sorted(result.scalars().all(), key=lambda log: log.id)
    assert len(logs) == 2
    assert logs[1].status == "rate_limited"
    assert logs[1].error_code == "RATE_LIMIT"


async def test_usage_log_write_failure_does_not_break_rag_response(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB停止等でusage_logsの書き込みが失敗しても、推論レスポンスは200のまま返る
    (CLAUDE.md非交渉事項: ログ記録の失敗が推論レスポンスを壊してはならない)。"""
    await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    await isolate_model(embed_model)

    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )

    def _broken_session_maker() -> None:
        raise RuntimeError("DB接続不可(テストで意図的に発生させた障害)")

    monkeypatch.setattr("app.routers.rag.async_session_maker", _broken_session_maker)

    resp = await client.post("/api/rag/query", json={"question": "hi"})
    assert resp.status_code == 200
