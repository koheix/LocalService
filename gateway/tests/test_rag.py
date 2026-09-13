"""RAG検索(/api/rag/query)のテスト。バックエンドは FakeBackend。

`_pick_permitted_model` は `app.rag.pick_enabled_model` を介してid昇順で
決定的にモデルを選ぶ(index_documentと共通実装)。ただし共有DBに他のテストや
seed(chat-standard/embed-standard)が作った同種の有効なモデルが残っていると、
そちらの方がidが小さく先に選ばれてしまい、テスト対象のモデルが使われない。
conftest.py の `isolate_model`/`isolate_models` フィクスチャで、テスト対象
のモデル以外の同kindモデルを一時的に無効化し、テスト終了時に必ず元の状態へ
戻す(seedデータを壊したまま次のテスト/手動確認に影響させないため)。
"""

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
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
    # 埋め込みバックエンドに渡った実際の引数(質問文とbackend_name)を検証する。
    assert fake_embed.embed_calls == [(["関係ない質問"], embed_model.backend_name)]


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
    assert fake_embed.embed_calls == [(["GTX 1080のVRAMは？"], embed_model.backend_name)]
    assert len(body["citations"]) == 1


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


async def test_rag_query_usage_log_on_permission_denied_records_error_status(
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
    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )

    resp = await client.post("/api/rag/query", json={"question": "GTX 1080のVRAMは？"})
    assert resp.status_code == 403

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "rag")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "error"
    assert logs[0].error_code == "FORBIDDEN"
    # 403時点ではchatモデルは未確定なので、embed_model分として記録される。
    assert logs[0].model_id == embed_model.id


async def test_rag_query_concurrency_slot_released_after_permission_error(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    make_quota: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """403で終わる(finally節で例外送出される)リクエストでも同時実行スロットが

    解放されることを確認する。確認済み: routers/rag.py の
    `finally: concurrency_slots.release(user.id)` を削除すると、
    max_concurrent=1のもとで本テストの2回目のリクエストが429になり失敗する。
    """
    user = await login_as_new_user(role="user")
    embed_model = await make_model(kind="embedding")
    chat_model = await make_model(kind="chat", roles=("admin",))  # user ロールには権限なし
    await isolate_model(embed_model)
    await isolate_model(chat_model)
    await make_quota(user.id, max_concurrent=1)

    # 1件目: チャンクがあるのでchatモデル解決まで進み、権限エラーで403。
    await _add_chunk(db, user.id, _vec(1.0))
    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )
    resp1 = await client.post("/api/rag/query", json={"question": "GTX 1080のVRAMは？"})
    assert resp1.status_code == 403

    # 2件目: 直交ベクトルの質問で「関連文書なし」に倒す(chatモデル権限は不要)。
    # max_concurrent=1のままなので、1件目でスロットが解放されていなければ429になる。
    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0, index=1))
    )
    resp2 = await client.post("/api/rag/query", json={"question": "関係ない質問"})
    assert resp2.status_code == 200


async def test_rag_query_permission_denied_for_embedding_model(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await login_as_new_user(role="user")
    embed_model = await make_model(kind="embedding", roles=("admin",))  # user ロールには権限なし
    await isolate_model(embed_model)

    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )

    resp = await client.post("/api/rag/query", json={"question": "hi"})
    assert resp.status_code == 403


async def test_rag_query_chat_backend_raises_and_logs_error(
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

    class BrokenChatBackend(FakeBackend):
        async def chat(self, payload: dict) -> dict:
            self.chat_calls.append(payload)
            raise RuntimeError("チャットバックエンドが意図的に落ちたテスト用の障害")

    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: BrokenChatBackend())

    # httpxのASGITransportはハンドラ内の未処理例外を(500応答を送った後に)
    # 呼び出し元へ再送出する。実運用のuvicorn配下では素の500応答になる。
    with pytest.raises(RuntimeError):
        await client.post("/api/rag/query", json={"question": "GTX 1080のVRAMは？"})

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "rag")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "error"
    assert logs[0].error_code == "RuntimeError"
    assert logs[0].model_id == chat_model.id


async def test_rag_query_chat_backend_empty_choices_raises_and_logs_error(
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

    class EmptyChoicesChatBackend(FakeBackend):
        async def chat(self, payload: dict) -> dict:
            self.chat_calls.append(payload)
            return {"id": "fake", "object": "chat.completion", "model": "x", "choices": []}

    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: EmptyChoicesChatBackend())

    with pytest.raises(IndexError):
        await client.post("/api/rag/query", json={"question": "GTX 1080のVRAMは？"})

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "rag")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "error"
    assert logs[0].error_code == "IndexError"


def _vec_cos(cos_sim: float, *, dim: int = 1024) -> list[float]:
    """クエリベクトル _vec(1.0)(=e0)に対してコサイン類似度がおよそcos_simになる

    単位ベクトルを作る(コサイン距離はおよそ1-cos_sim)。chunks.embeddingは
    float4格納なので、厳密な等値比較が必要な境界値検証には使わないこと
    (下記test_rag_query_distance_threshold_boundary_is_inclusiveのコメント参照)。
    """
    v = [0.0] * dim
    v[0] = cos_sim
    v[1] = (max(0.0, 1.0 - cos_sim * cos_sim)) ** 0.5
    return v


async def test_rag_query_distance_threshold_boundary_is_inclusive(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """距離ちょうど閾値は含み、閾値を超えるものは除外されることを検証する。

    `settings.rag_distance_threshold` を0.0に固定し、クエリと完全に同一の
    ベクトル(コサイン距離は厳密に0.0。1.0と0.0はfloat32で誤差なく表現できる
    ので丸め誤差の影響を受けない)を「境界ちょうど」として使う。cos_simを
    0.4/0.6等の中途半端な値にすると、chunks.embeddingがfloat4格納のため
    丸め誤差で厳密に閾値と一致せず、`<=`を`<`に変えても検知できない
    (実際にこの問題が過去のバージョンにあった)。

    確認済み: routers/rag.py の `distance <= settings.rag_distance_threshold`
    を `<` に変えると、境界の"boundary-in"が誤って除外され本テストが失敗する。
    """
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    chat_model = await make_model(kind="chat")
    await isolate_model(embed_model)
    await isolate_model(chat_model)

    monkeypatch.setattr(get_settings(), "rag_distance_threshold", 0.0)

    await _add_chunk(db, user.id, _vec(1.0), content="boundary-in")  # クエリと同一 -> distance 0.0
    await _add_chunk(db, user.id, _vec(1.0, index=1), content="far")  # 直交 -> distance 1.0

    fake_embed = ControlledEmbedBackend(_vec(1.0))
    fake_chat = FakeBackend()
    monkeypatch.setattr("app.routers.rag.get_embed_backend", lambda: fake_embed)
    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: fake_chat)

    resp = await client.post("/api/rag/query", json={"question": "hi"})
    assert resp.status_code == 200
    body = resp.json()
    contents = [c["content"] for c in body["citations"]]
    assert contents == ["boundary-in"]


async def test_rag_query_limits_to_top_k(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """settings.rag_top_k(既定4)を超えるチャンクがあっても、上位k件に絞られる。"""
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    chat_model = await make_model(kind="chat")
    await isolate_model(embed_model)
    await isolate_model(chat_model)

    # 全て閾値内(distance<=0.6)だが5件あり、rag_top_k=4なので最も遠い1件は除外される。
    for i, cos_sim in enumerate([1.0, 0.95, 0.9, 0.85, 0.8]):
        await _add_chunk(db, user.id, _vec_cos(cos_sim), content=f"c{i}")

    fake_embed = ControlledEmbedBackend(_vec(1.0))
    fake_chat = FakeBackend()
    monkeypatch.setattr("app.routers.rag.get_embed_backend", lambda: fake_embed)
    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: fake_chat)

    resp = await client.post("/api/rag/query", json={"question": "hi"})
    assert resp.status_code == 200
    body = resp.json()
    contents = [c["content"] for c in body["citations"]]
    assert contents == ["c0", "c1", "c2", "c3"]  # 最も遠いc4(cos_sim=0.8)は含まれない


async def test_rag_query_via_api_key_records_api_key_id(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    new_client: Callable[[], AsyncClient],
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    await isolate_model(embed_model)
    created = await client.post("/api/auth/api-keys", json={"name": "k"})
    key_id = created.json()["id"]
    raw_key = created.json()["key"]

    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )

    async with new_client() as c2:
        resp = await c2.post(
            "/api/rag/query",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={"question": "hi"},
        )
        assert resp.status_code == 200

    result = await db.execute(select(UsageLog).where(UsageLog.api_key_id == key_id))
    logs = result.scalars().all()
    assert len(logs) == 1


async def test_rag_query_picks_lowest_id_embedding_model_deterministically(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    isolate_models: Callable[[list[Model]], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有効なembeddingモデルが複数あっても、常にid最小のものが選ばれること。

    確認済み: app/rag.py の pick_enabled_model の `.order_by(Model.id)` を
    `.order_by(Model.id.desc())` に反転すると、本テストが失敗する
    (このテストを追加する前は、isolate_modelで同kindモデルを常に1つに
    絞っていたためこの反転が検知できなかった)。
    """
    await login_as_new_user()
    model_low = await make_model(kind="embedding", backend_name="fake-embed-low")
    model_high = await make_model(kind="embedding", backend_name="fake-embed-high")
    assert model_low.id < model_high.id
    await isolate_models([model_low, model_high])

    fake_embed = FakeBackend()
    monkeypatch.setattr("app.routers.rag.get_embed_backend", lambda: fake_embed)

    resp = await client.post("/api/rag/query", json={"question": "hi"})
    assert resp.status_code == 200
    assert len(fake_embed.embed_calls) == 1
    assert fake_embed.embed_calls[0][1] == model_low.backend_name
