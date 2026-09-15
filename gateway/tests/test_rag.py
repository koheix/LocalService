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
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.main import app
from app.models import Chunk, Document, Model, UsageLog
from tests.conftest import FakeBackend


def _vec(value: float, *, index: int = 0, dim: int = 1024) -> list[float]:
    v = [0.0] * dim
    v[index] = value
    return v


async def _read_rag_events(resp: Any) -> list[dict]:
    """RAGのSSEストリームを読み切り、イベント(dict)のリストを返す。

    `data: [DONE]`で終端することを契約(docs/API.md)として固定するため、
    最後まで読んでも[DONE]が来なければ失敗させる(黙って空リストや途中の
    イベント列を返さない)。[DONE]の後にさらにイベントが来た場合も、
    それを黙って集約せず失敗させる([DONE]は最後の1回だけの契約)。
    """
    events: list[dict] = []
    saw_done = False
    async for chunk in resp.aiter_bytes():
        for line in chunk.decode("utf-8").splitlines():
            line = line.strip()
            if not line.startswith("data: "):
                continue
            assert not saw_done, "data: [DONE] の後にさらにイベントが送られている"
            payload = line[len("data: ") :]
            if payload == "[DONE]":
                saw_done = True
                continue
            events.append(json.loads(payload))
    assert saw_done, "ストリームが data: [DONE] で終端していない"
    return events


def _event_types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


def _answer_from_events(events: list[dict]) -> str:
    return "".join(e["content"] for e in events if e["type"] == "delta")


def _citations_from_events(events: list[dict]) -> list[dict]:
    """citationsイベントを取り出す。ちょうど1回だけ送られる契約(docs/API.md)なので、

    無い/複数あるのはどちらもテスト対象の異常として素通しせず失敗させる。
    """
    matches = [e["citations"] for e in events if e["type"] == "citations"]
    assert len(matches) == 1, f"citationsイベントはちょうど1回のはずが{len(matches)}回だった"
    return matches[0]


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

    citations が空になり、かつチャット推論が一度も呼ばれないこと、
    さらにストリーミングを一切使わず即座にJSON応答が返ることを確認する。

    確認済み: routers/rag.py の距離閾値判定を無効化(settings.rag_distance_threshold
    を極端に大きくする)と、本テストが `resp.headers["content-type"]` の
    アサーション(SSEに切り替わる)、または `fake_chat.chat_stream_calls == []`
    (チャット推論が呼ばれてしまう)のいずれかで失敗する。
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
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["citations"] == []
    assert "見つかりません" in body["answer"]
    # T-27でchat()は使われなくなった(常にchat_stream())。関連文書なし判定が
    # 壊れてLLMが呼ばれてしまった場合はここで検知する。
    assert fake_chat.chat_calls == []
    assert fake_chat.chat_stream_calls == []
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

    async with client.stream(
        "POST", "/api/rag/query", json={"question": "GTX 1080のVRAMは？"}
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = await _read_rag_events(resp)

    # イベント順序を完全一致で固定する: status → delta(1回、FakeBackend既定チャンク
    # のうちcontentを持つのは1個だけ) → citations。citationsがdeltaより先に来る
    # (回答が全部届く前に引用が確定する)といった契約違反(docs/API.md)を検知できる。
    assert _event_types(events) == ["status", "delta", "citations"]
    assert events[0] == {"type": "status", "phase": "generating"}
    assert _answer_from_events(events) == "fake"  # FakeBackend.chat_stream() の既定チャンク
    citations = _citations_from_events(events)
    citation_ids = [c["chunk_id"] for c in citations]
    assert chunk.id in citation_ids
    match = next(c for c in citations if c["chunk_id"] == chunk.id)
    assert match["document_id"] == document.id
    assert match["filename"] == document.filename
    assert len(citations) == 1

    # バックエンドに渡った実際のpayloadを検証する(served_nameではなくbackend_nameで送る, D-003)。
    assert len(fake_chat.chat_stream_calls) == 1
    sent = fake_chat.chat_stream_calls[0]
    assert sent["model"] == chat_model.backend_name
    assert chunk.content in sent["messages"][1]["content"]
    assert "GTX 1080のVRAMは？" in sent["messages"][1]["content"]
    assert sent["stream"] is True
    # with_stream_usage()が付与するオプション。これが無いと実際のOllamaは
    # usageを返さず、usage_logsのprompt_tokens/completion_tokensが常に0に
    # なる(FakeBackendは無視して常にusageを返すため、これを外しても
    # 他のusage_logs系アサーションだけでは検知できない)。
    assert sent["stream_options"] == {"include_usage": True}
    assert fake_embed.embed_calls == [(["GTX 1080のVRAMは？"], embed_model.backend_name)]


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


async def test_rag_query_concurrency_slot_held_during_answer_streaming(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    make_quota: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-27でストリーミング化した後も、回答生成(chat_stream)が完走するまで

    同時実行スロットが保持され続けること(検索フェーズだけでなく生成フェーズも
    スロットの対象)。

    確認済み(両方の変異を実際に当てて検証): rag.pyの`_wrapped_stream`内
    `finally`の`concurrency_slots.release`を削除すると、スロットが永久に
    解放されなくなり、本テストの2本目(resp2, 429を期待)ではなく3本目
    (resp3, 200を期待)が失敗する。逆に、`concurrency_slots.acquire`の
    直後に`concurrency_slots.release`を呼ぶよう早めてスロットをすぐ手放す
    と、2本目のリクエストはスロットを正常に取得できてしまい、`resp2`への
    アサーション失敗(429を期待して200になる)ではなく、その`resp2`用の
    `BlockingChatBackend`インスタンスも同じ`release`(まだset()されていない)
    でブロックしたまま`asyncio.wait_for(..., timeout=5)`がタイムアウトする
    形で失敗する。

    同期は`BlockingChatBackend`が最初のdeltaを送った直後、実際に
    `release.wait()`でブロックする直前にサーバー側で`asyncio.Event`を
    セットすることで行う(httpx ASGITransport越しに`client.stream()`で
    クライアント側からバイト列を観測しようとすると、このテスト環境では
    アプリ側タスクがブロックするまでクライアント側の`__aenter__`/
    `aiter_bytes()`自体が一切進行しないため、クライアント観測ベースの
    同期は使えないことを実験で確認した)。
    `hold_stream_open`はストリームを1チャンクだけ読んで`break`せず、
    最後まで(DONEが届くまで)読み切る(接続を早期に閉じるとGeneratorExitで
    `_wrapped_stream`の`finally`が即座に走ってしまい、「生成完了まで
    保持され続ける」ことの検証にならないため)。
    """
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    chat_model = await make_model(kind="chat")
    await isolate_model(embed_model)
    await isolate_model(chat_model)
    await make_quota(user.id, max_concurrent=1)
    await _add_chunk(db, user.id, _vec(1.0))

    release = asyncio.Event()
    generating_blocked = asyncio.Event()

    class BlockingChatBackend(FakeBackend):
        async def chat_stream(self, payload: dict):
            self.chat_stream_calls.append(payload)
            yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
            generating_blocked.set()
            await release.wait()
            yield b"data: [DONE]\n\n"

    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )
    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: BlockingChatBackend())

    async def hold_stream_open() -> None:
        async with client.stream(
            "POST", "/api/rag/query", json={"question": "GTX 1080のVRAMは？"}
        ) as resp:
            assert resp.status_code == 200
            # 早期にbreak/close せず、ストリームが自然に完走する(DONEまで届く)
            # まで読み切る。
            async for _ in resp.aiter_bytes():
                pass

    task = asyncio.create_task(hold_stream_open())
    try:
        await asyncio.wait_for(generating_blocked.wait(), timeout=5)
        # ここまでで、サーバー側はdeltaを1つ送った後 release.wait() で
        # ブロック中。同時実行スロットは`_wrapped_stream`がまだ完走して
        # いない=保持されたままのはず。
        resp2 = await asyncio.wait_for(
            client.post("/api/rag/query", json={"question": "hi"}), timeout=5
        )
        assert resp2.status_code == 429
        assert resp2.json()["error"]["code"] == "RATE_LIMIT"
        # rpm制限(既定60/min)による429と区別する。同時実行制限のRetry-Afterは
        # rag.py側で固定で"1"を返す(rpm制限は別途61秒相当になる)。
        assert resp2.headers["Retry-After"] == "1"
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=5)

    # ストリーム完了後はスロットが解放され、次のリクエストは成功する
    # (release済みのため、同じBlockingChatBackendでも即座に完走する)。
    resp3 = await asyncio.wait_for(
        client.post("/api/rag/query", json={"question": "hi"}), timeout=5
    )
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

    async with client.stream(
        "POST", "/api/rag/query", json={"question": "GTX 1080のVRAMは？"}
    ) as resp:
        assert resp.status_code == 200
        await _read_rag_events(resp)  # ストリームを読み切ってfinally節を実行させる

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "rag")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "ok"
    assert logs[0].model_id == chat_model.id
    # FakeBackend.chat_stream() の固定usage(prompt_tokens=5, completion_tokens=3)。
    # prompt_tokensも見ないと、rag.pyがusageチャンクのprompt_tokens取り込みを
    # 落としても(常に0のまま記録されても)検知できない。
    assert logs[0].prompt_tokens == 5
    assert logs[0].completion_tokens == 3
    # T-27でストリーミング化したことで、/api/v1・/api/conversationsと同様に
    # ttft_msが実測されるようになったこと(常にNoneのままではないこと)を固定する。
    assert logs[0].ttft_ms is not None


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


async def test_rag_query_embed_backend_unavailable_returns_503_and_logs_error(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """埋め込みバックエンド(Ollama)停止等のBackendUnavailableErrorは、

    他のAppErrorと同様にJSONエンベロープ付きの503として返り、
    usage_logsにも記録されること(埋め込み側の異常応答は従来未検証だった)。
    """
    from app.errors import BackendUnavailableError

    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    await isolate_model(embed_model)

    class UnavailableEmbedBackend(FakeBackend):
        async def embed(self, texts: list[str], model: str) -> list[list[float]]:
            raise BackendUnavailableError("Ollama停止中(テスト用に発生させた障害)")

    monkeypatch.setattr("app.routers.rag.get_embed_backend", lambda: UnavailableEmbedBackend())

    resp = await client.post("/api/rag/query", json={"question": "hi"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "BACKEND_UNAVAILABLE"

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "rag")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "error"
    assert logs[0].error_code == "BACKEND_UNAVAILABLE"
    assert logs[0].model_id == embed_model.id


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
        async def chat_stream(self, payload: dict):
            self.chat_stream_calls.append(payload)
            yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
            raise RuntimeError("チャットバックエンドが意図的に落ちたテスト用の障害")

    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: BrokenChatBackend())

    # docs/API.mdの契約(200を返した後にストリーム本体側で例外が起きて接続が
    # 異常終了する)を実際に検証する。既定のclientフィクスチャは
    # ASGITransport(raise_app_exceptions=True、デバッグ用の既定値)なので、
    # アプリ内の未処理例外がテストコードへそのまま再送出され、
    # `client.stream()`の`__aenter__`自体が失敗してresp/status_codeに
    # 一切アクセスできない(実験で確認済み)。これでは「200が返った後で
    # 途中終了する」ことを検証できないため、この1点だけ
    # raise_app_exceptions=Falseの別クライアントを使う。これは実運用の
    # uvicorn配下での実際の挙動(未処理例外は、ヘッダ送信済みなら200のまま
    # 本文が途中で終わり、そうでなければ500になる)により近い。
    lenient_transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=lenient_transport, base_url="http://testserver", cookies=client.cookies
    ) as lenient_client:
        async with lenient_client.stream(
            "POST", "/api/rag/query", json={"question": "GTX 1080のVRAMは？"}
        ) as resp:
            assert resp.status_code == 200  # ヘッダは正常に送信済み
            body = b"".join([c async for c in resp.aiter_bytes()])

    assert b'"type": "status"' in body  # 最初のイベントまでは届いている
    assert b'"type": "delta"' in body
    assert b"[DONE]" not in body  # 正常完走せず途中で切れている(citationsも届かない)

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "rag")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "error"
    assert logs[0].error_code == "RuntimeError"
    assert logs[0].model_id == chat_model.id


async def test_rag_query_stream_ignores_chunks_without_choices(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """バックエンドが`choices`が空/欠落したチャンクや壊れたJSON行を混ぜて

    送ってきても、(非ストリーミングだった旧実装のように`choices[0]`で例外に
    ならず)無視して残りのdeltaは正常に処理され、ストリームが最後まで完走
    すること。

    期待する回答文字列("アルファ")はFakeBackend.chat_stream()の既定チャンクが
    出す"fake"とは別物にしてある。既定チャンクにフォールバックしても
    このアサーションだけは偶然一致しないようにするため。
    """
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    chat_model = await make_model(kind="chat")
    await isolate_model(embed_model)
    await isolate_model(chat_model)

    await _add_chunk(db, user.id, _vec(1.0))
    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )

    def _chunk(obj: dict) -> bytes:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()

    flaky = FakeBackend()
    flaky.chat_stream_chunks = [
        _chunk({"choices": [{"delta": {"content": "アル"}}]}),
        b'data: {"choices":[]}\n\n',  # choicesが空(例: keep-aliveチャンク等)
        b'data: {"model":"x"}\n\n',  # choicesキー自体が無い
        b"data: {not valid json\n\n",  # 壊れたJSON行
        _chunk({"choices": [{"delta": {"content": "ファ"}}]}),
        b"data: [DONE]\n\n",
    ]
    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: flaky)

    async with client.stream(
        "POST", "/api/rag/query", json={"question": "GTX 1080のVRAMは？"}
    ) as resp:
        assert resp.status_code == 200
        events = await _read_rag_events(resp)

    assert len(flaky.chat_stream_calls) == 1  # 差し替えたbackendが実際に呼ばれたこと
    assert _answer_from_events(events) == "アルファ"
    assert len(_citations_from_events(events)) == 1

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "rag")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "ok"


async def test_rag_query_stream_reassembles_delta_split_across_chunks(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """バックエンドのSSEチャンク境界が`data:`行の途中で分割されても、

    内容を欠落させずに再構成できること。RAGは/api/v1・/api/conversationsと
    異なり生バイト列を素通ししてクライアント側で再組み立てさせるのではなく、
    サーバー側(rag.py)がフロント向けJSONイベントの唯一の生成元になるため、
    ここでの行再構成が壊れると回答が静かに欠落する。

    確認済み: rag.pyの`_wrapped_stream`内の行バッファリング
    (`line_buffer += raw_chunk.decode(...)`)をやめて、各チャンクを個別に
    `raw_chunk.decode().split("\\n")`するだけの実装に戻すと、本テストの
    `_answer_from_events(events) == "アルファ"`が失敗する(分断された
    JSON行がjson.JSONDecodeErrorとして握りつぶされ、"アル"の分が
    欠落した"ファ"だけになるため)。
    """
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    chat_model = await make_model(kind="chat")
    await isolate_model(embed_model)
    await isolate_model(chat_model)
    await _add_chunk(db, user.id, _vec(1.0))
    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )

    full_line = "data: " + json.dumps(
        {"choices": [{"delta": {"content": "アルファ"}}]}, ensure_ascii=False
    )
    split_at = len(full_line) // 2
    assert 0 < split_at < len(full_line)  # 本当に行の途中で割れていることの前提確認

    split_backend = FakeBackend()
    split_backend.chat_stream_chunks = [
        full_line[:split_at].encode(),  # 改行を含まない、行の途中で切れたチャンク
        (full_line[split_at:] + "\n\n").encode(),  # 残りの半分 + 行末
        b"data: [DONE]\n\n",
    ]
    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: split_backend)

    async with client.stream(
        "POST", "/api/rag/query", json={"question": "GTX 1080のVRAMは？"}
    ) as resp:
        assert resp.status_code == 200
        events = await _read_rag_events(resp)

    assert _answer_from_events(events) == "アルファ"
    assert len(_citations_from_events(events)) == 1


async def test_rag_query_stream_flushes_trailing_line_without_newline(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """バックエンドの最後のチャンクが改行(`\\n\\n`)で終わらないまま

    `chat_stream()`が終了しても、その行のdeltaが失われないこと
    (ループ終了後のバッファflush、rag.py参照)。

    確認済み: rag.pyの`_wrapped_stream`のループ後の
    `if line_buffer.strip(): ...`フラッシュ処理を削除すると、本テストの
    `_answer_from_events(events) == "ラスト"`が失敗する(末尾のdeltaが
    バッファに残ったまま破棄され、空文字列になるため)。
    """
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    chat_model = await make_model(kind="chat")
    await isolate_model(embed_model)
    await isolate_model(chat_model)
    await _add_chunk(db, user.id, _vec(1.0))
    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )

    no_trailing_newline = FakeBackend()
    no_trailing_newline.chat_stream_chunks = [
        (
            "data: "
            + json.dumps({"choices": [{"delta": {"content": "ラスト"}}]}, ensure_ascii=False)
        ).encode()
        # 意図的に末尾に \n\n を付けず、これで chat_stream() を終わらせる。
    ]
    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: no_trailing_newline)

    async with client.stream(
        "POST", "/api/rag/query", json={"question": "GTX 1080のVRAMは？"}
    ) as resp:
        assert resp.status_code == 200
        events = await _read_rag_events(resp)

    assert _answer_from_events(events) == "ラスト"
    assert len(_citations_from_events(events)) == 1


async def test_rag_query_usage_log_records_realistic_ttft(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ttft_msが単に非Noneであるだけでなく、実測に近い値であることを固定する。

    (`is not None`だけの検証だと、rag.pyがttft_msを0固定にする退行を
    見逃す。)最初のdeltaの前に意図的な遅延を入れ、ttft_msがその遅延以上、
    かつ全体のlatency_ms以下であることを検証する。
    """
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    chat_model = await make_model(kind="chat")
    await isolate_model(embed_model)
    await isolate_model(chat_model)
    await _add_chunk(db, user.id, _vec(1.0))
    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )

    class SlowFirstChunkBackend(FakeBackend):
        async def chat_stream(self, payload: dict):
            await asyncio.sleep(0.05)
            async for chunk in super().chat_stream(payload):
                yield chunk

    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: SlowFirstChunkBackend())

    async with client.stream(
        "POST", "/api/rag/query", json={"question": "GTX 1080のVRAMは？"}
    ) as resp:
        assert resp.status_code == 200
        await _read_rag_events(resp)

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "rag")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].ttft_ms is not None
    assert logs[0].ttft_ms >= 50
    assert logs[0].ttft_ms <= logs[0].latency_ms


async def test_rag_query_client_disconnect_still_logs_usage(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """クライアント切断等でリクエストのタスク自体がキャンセルされても、

    asyncio.shieldによりusage_logsへの記録が失われないこと
    (test_v1.pyのtest_chat_completions_client_disconnect_still_logs_usageと
    同型のRAG版。rag.pyの`_wrapped_stream`のfinally節のコメントが指す挙動)。

    確認済み: rag.pyの該当`asyncio.shield(...)`を外して本テストのみ実行すると
    `assert len(logs) == 1`が`0 == 1`で失敗する(記録が失われる)。元に戻すと
    成功する。
    """
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    chat_model = await make_model(kind="chat")
    await isolate_model(embed_model)
    await isolate_model(chat_model)
    await _add_chunk(db, user.id, _vec(1.0))
    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )

    started = asyncio.Event()

    class SlowBackend(FakeBackend):
        async def chat_stream(self, payload: dict):
            self.chat_stream_calls.append(payload)
            yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
            started.set()
            await asyncio.sleep(30)  # 通常は到達しない。外側でタスクごとキャンセルする。
            yield b"data: [DONE]\n\n"

    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: SlowBackend())

    async def do_request() -> None:
        async with client.stream(
            "POST", "/api/rag/query", json={"question": "GTX 1080のVRAMは？"}
        ) as resp:
            async for _ in resp.aiter_bytes():
                pass

    task = asyncio.create_task(do_request())
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # shieldされたバックグラウンドの書き込みが完了するのを少し待つ。
    for _ in range(20):
        result = await db.execute(
            select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "rag")
        )
        if result.scalars().all():
            break
        await asyncio.sleep(0.05)

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "rag")
    )
    logs = result.scalars().all()
    assert len(logs) == 1


async def test_rag_query_stream_completes_with_empty_answer(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    db: AsyncSession,
    isolate_model: Callable[[Model], Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """バックエンドが有効なdeltaを1つも送らずに完走した場合でも、

    (現状の設計判断として)エラーにはせず、citationsまで正常に届いて
    okとして完走すること。回答が空のままユーザーに何も表示されない問題への
    対応はフロントエンド側(QuestionPanel、T-27レビュー指摘)で行っている。
    """
    user = await login_as_new_user()
    embed_model = await make_model(kind="embedding")
    chat_model = await make_model(kind="chat")
    await isolate_model(embed_model)
    await isolate_model(chat_model)
    await _add_chunk(db, user.id, _vec(1.0))
    monkeypatch.setattr(
        "app.routers.rag.get_embed_backend", lambda: ControlledEmbedBackend(_vec(1.0))
    )

    empty = FakeBackend()
    empty.chat_stream_chunks = [
        b'data: {"choices":[{"delta":{}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    monkeypatch.setattr("app.routers.rag.get_chat_backend", lambda: empty)

    async with client.stream(
        "POST", "/api/rag/query", json={"question": "GTX 1080のVRAMは？"}
    ) as resp:
        assert resp.status_code == 200
        events = await _read_rag_events(resp)

    assert _event_types(events) == ["status", "citations"]  # deltaが1つも無い
    assert _answer_from_events(events) == ""
    assert len(_citations_from_events(events)) == 1

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "rag")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "ok"


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

    async with client.stream("POST", "/api/rag/query", json={"question": "hi"}) as resp:
        assert resp.status_code == 200
        events = await _read_rag_events(resp)
    contents = [c["content"] for c in _citations_from_events(events)]
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

    async with client.stream("POST", "/api/rag/query", json={"question": "hi"}) as resp:
        assert resp.status_code == 200
        events = await _read_rag_events(resp)
    contents = [c["content"] for c in _citations_from_events(events)]
    assert contents == ["c0", "c1", "c2", "c3"]  # 最も遠いc4(cos_sim=0.8)は含まれない

    # citations に4件返るだけでなく、LLMへのプロンプトにも4件全ての内容・
    # 出典名・ラベル番号が「対応関係を保ったまま」渡っていること
    # (citationsとLLMへの文脈が食い違わない)。部分一致だけの検証だと、
    # 出典名を丸ごと落とす・順序を入れ替える、といった劣化を検知できない
    # ため、完全一致で固定する。
    assert len(fake_chat.chat_stream_calls) == 1
    sent_content = fake_chat.chat_stream_calls[0]["messages"][1]["content"]
    expected_context = "\n\n".join(f"[出典{n}: pytest-rag-doc.txt]\nc{n - 1}" for n in range(1, 5))
    assert sent_content == f"# コンテキスト\n{expected_context}\n\n# 質問\nhi"


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
