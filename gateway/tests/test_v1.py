"""OpenAI 互換エンドポイント(/api/v1/*)のテスト。バックエンドは FakeBackend。"""

import asyncio
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageLog
from tests.conftest import FakeBackend


async def test_list_models_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/models")
    assert resp.status_code == 401


async def test_list_models_returns_served_name(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user()
    model = await make_model()

    resp = await client.get("/api/v1/models")
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["data"]]
    assert model.served_name in ids


async def test_chat_completions_success(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    fake_backend: FakeBackend,
) -> None:
    await login_as_new_user()
    model = await make_model()

    resp = await client.post(
        "/api/v1/chat/completions",
        json={"model": model.served_name, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "fake reply"
    # served_name のまま返ること(内部実体名 backend_name を漏らさない, D-003)
    assert body["model"] == model.served_name

    # バックエンドに渡った実際の payload を検証する。
    assert len(fake_backend.chat_calls) == 1
    sent = fake_backend.chat_calls[0]
    assert sent["model"] == model.backend_name
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


async def test_chat_completions_streaming(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    fake_backend: FakeBackend,
) -> None:
    await login_as_new_user()
    model = await make_model()

    async with client.stream(
        "POST",
        "/api/v1/chat/completions",
        json={
            "model": model.served_name,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as resp:
        assert resp.status_code == 200
        chunks = [chunk async for chunk in resp.aiter_bytes()]
    full = b"".join(chunks)
    assert b"fake" in full
    assert full.strip().endswith(b"data: [DONE]")
    # served_name に書き換わっていること、backend_name のままではないこと
    assert model.served_name.encode() in full
    assert model.backend_name.encode() not in full

    assert len(fake_backend.chat_stream_calls) == 1
    sent = fake_backend.chat_stream_calls[0]
    assert sent["model"] == model.backend_name
    # usage を取得するため stream_options を明示的に付けていること
    assert sent["stream_options"] == {"include_usage": True}


async def test_chat_completions_multi_chunk_content_is_concatenated(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    fake_backend: FakeBackend,
) -> None:
    await login_as_new_user()
    model = await make_model()
    fake_backend.chat_stream_chunks = [
        b'data: {"choices":[{"delta":{"content":"fa"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"ke"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":" reply"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]

    async with client.stream(
        "POST",
        "/api/v1/chat/completions",
        json={
            "model": model.served_name,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as resp:
        chunks = [chunk async for chunk in resp.aiter_bytes()]
    full = b"".join(chunks).decode()
    assert '"content": "fa"' in full
    assert '"content": "ke"' in full
    assert '"content": " reply"' in full


async def test_chat_completions_unknown_model_404(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    await login_as_new_user()
    resp = await client.post(
        "/api/v1/chat/completions",
        json={"model": "no-such-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["type"] == "not_found"
    assert body["error"]["code"] == "NOT_FOUND"


async def test_chat_completions_permission_denied_403(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user(role="user")
    model = await make_model(roles=("admin",))  # user ロールには権限を付与しない

    resp = await client.post(
        "/api/v1/chat/completions",
        json={"model": model.served_name, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["type"] == "permission_error"
    assert body["error"]["code"] == "FORBIDDEN"


async def test_embeddings_success(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    fake_backend: FakeBackend,
) -> None:
    await login_as_new_user()
    model = await make_model(kind="embedding")

    resp = await client.post(
        "/api/v1/embeddings", json={"model": model.served_name, "input": "hello"}
    )
    assert resp.status_code == 200
    assert len(resp.json()["data"][0]["embedding"]) == 1024

    assert fake_backend.embed_calls == [(["hello"], model.backend_name)]


async def test_embeddings_wrong_kind_404(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user()
    chat_model = await make_model(kind="chat")

    resp = await client.post(
        "/api/v1/embeddings", json={"model": chat_model.served_name, "input": "hello"}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# レート制限・同時実行制限(429)
# ---------------------------------------------------------------------------


async def test_chat_completions_rate_limited_429(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    make_quota: Callable,
) -> None:
    user = await login_as_new_user()
    model = await make_model()
    await make_quota(user.id, rpm_limit=2)

    payload = {"model": model.served_name, "messages": [{"role": "user", "content": "hi"}]}
    r1 = await client.post("/api/v1/chat/completions", json=payload)
    r2 = await client.post("/api/v1/chat/completions", json=payload)
    r3 = await client.post("/api/v1/chat/completions", json=payload)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    assert "Retry-After" in r3.headers
    body = r3.json()
    assert body["error"]["code"] == "RATE_LIMIT"
    assert body["error"]["type"] == "rate_limit_exceeded"


async def test_chat_completions_concurrency_limited_429(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    make_quota: Callable,
    fake_backend: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await login_as_new_user()
    model = await make_model()
    await make_quota(user.id, max_concurrent=1)

    release = asyncio.Event()
    first_chunk_sent = asyncio.Event()

    class BlockingBackend(FakeBackend):
        async def chat_stream(self, payload: dict):
            self.chat_stream_calls.append(payload)
            yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
            first_chunk_sent.set()
            await release.wait()
            yield b"data: [DONE]\n\n"

    blocking = BlockingBackend()
    monkeypatch.setattr("app.routers.v1.get_chat_backend", lambda: blocking)

    payload = {
        "model": model.served_name,
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }

    async def hold_first_request() -> None:
        async with client.stream("POST", "/api/v1/chat/completions", json=payload) as resp:
            assert resp.status_code == 200
            async for _ in resp.aiter_bytes():
                break  # 最初のチャンクを受け取った時点でスロットは保持されている

    task = asyncio.create_task(hold_first_request())
    try:
        await asyncio.wait_for(first_chunk_sent.wait(), timeout=5)

        resp2 = await client.post("/api/v1/chat/completions", json=payload)
        assert resp2.status_code == 429
        assert resp2.json()["error"]["code"] == "RATE_LIMIT"
    finally:
        release.set()
        await task


# ---------------------------------------------------------------------------
# usage_logs 記録(成功・失敗とも)
# ---------------------------------------------------------------------------


async def test_chat_completions_usage_log_on_success(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable, db: AsyncSession
) -> None:
    user = await login_as_new_user()
    model = await make_model()

    resp = await client.post(
        "/api/v1/chat/completions",
        json={"model": model.served_name, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200

    result = await db.execute(select(UsageLog).where(UsageLog.user_id == user.id))
    logs = result.scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.app == "api"
    assert log.status == "ok"
    assert log.error_code is None
    assert log.model_id == model.id
    assert log.prompt_tokens == 5
    assert log.completion_tokens == 3


async def test_chat_completions_usage_log_on_unknown_model(
    client: AsyncClient, login_as_new_user: Callable, db: AsyncSession
) -> None:
    user = await login_as_new_user()

    resp = await client.post(
        "/api/v1/chat/completions",
        json={"model": "no-such-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 404

    result = await db.execute(select(UsageLog).where(UsageLog.user_id == user.id))
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "error"
    assert logs[0].error_code == "NOT_FOUND"
    assert logs[0].model_id is None


async def test_chat_completions_usage_log_on_permission_denied(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable, db: AsyncSession
) -> None:
    user = await login_as_new_user(role="user")
    model = await make_model(roles=("admin",))

    resp = await client.post(
        "/api/v1/chat/completions",
        json={"model": model.served_name, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 403

    result = await db.execute(select(UsageLog).where(UsageLog.user_id == user.id))
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "error"
    assert logs[0].error_code == "FORBIDDEN"


async def test_chat_completions_usage_log_on_rate_limited(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    make_quota: Callable,
    db: AsyncSession,
) -> None:
    user = await login_as_new_user()
    model = await make_model()
    await make_quota(user.id, rpm_limit=1)

    payload = {"model": model.served_name, "messages": [{"role": "user", "content": "hi"}]}
    await client.post("/api/v1/chat/completions", json=payload)
    resp2 = await client.post("/api/v1/chat/completions", json=payload)
    assert resp2.status_code == 429

    result = await db.execute(select(UsageLog).where(UsageLog.user_id == user.id))
    logs = sorted(result.scalars().all(), key=lambda log: log.id)
    assert len(logs) == 2
    assert logs[1].status == "rate_limited"
    assert logs[1].error_code == "RATE_LIMIT"


async def test_chat_completions_streaming_usage_log(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable, db: AsyncSession
) -> None:
    user = await login_as_new_user()
    model = await make_model()

    async with client.stream(
        "POST",
        "/api/v1/chat/completions",
        json={
            "model": model.served_name,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as resp:
        async for _ in resp.aiter_bytes():
            pass

    result = await db.execute(select(UsageLog).where(UsageLog.user_id == user.id))
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "ok"
    assert logs[0].prompt_tokens == 5
    assert logs[0].completion_tokens == 3
    assert logs[0].ttft_ms is not None
    assert logs[0].ttft_ms >= 0


async def test_usage_log_write_failure_does_not_break_response(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB停止等でusage_logsの書き込みが失敗しても、推論レスポンスは200のまま返る
    (CLAUDE.md非交渉事項: ログ記録の失敗が推論レスポンスを壊してはならない)。"""
    await login_as_new_user()
    model = await make_model()

    def _broken_session_maker() -> None:
        raise RuntimeError("DB接続不可(テストで意図的に発生させた障害)")

    monkeypatch.setattr("app.routers.v1.async_session_maker", _broken_session_maker)

    resp = await client.post(
        "/api/v1/chat/completions",
        json={"model": model.served_name, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "fake reply"
