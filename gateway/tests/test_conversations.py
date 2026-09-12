"""会話CRUDとチャット送信APIのテスト。バックエンドは FakeBackend。"""

from collections.abc import Callable

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageLog
from tests.conftest import FakeBackend


async def test_create_and_list_conversation(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user()
    model = await make_model()

    created = await client.post(
        "/api/conversations", json={"title": "テスト", "model": model.served_name}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["model"] == model.served_name

    listed = await client.get("/api/conversations")
    assert listed.status_code == 200
    assert any(c["id"] == body["id"] for c in listed.json())


async def test_create_conversation_unknown_model_404(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    await login_as_new_user()
    resp = await client.post("/api/conversations", json={"model": "no-such-model"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["type"] == "not_found"
    assert body["error"]["code"] == "NOT_FOUND"


async def test_update_conversation_settings(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user()
    model = await make_model()
    created = await client.post("/api/conversations", json={"model": model.served_name})
    conv_id = created.json()["id"]
    original_updated_at = created.json()["updated_at"]

    patched = await client.patch(
        f"/api/conversations/{conv_id}",
        json={"system_prompt": "簡潔に答えて", "temperature": 0.2, "top_p": 0.8, "max_tokens": 256},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["system_prompt"] == "簡潔に答えて"
    assert body["temperature"] == 0.2
    assert body["top_p"] == 0.8
    assert body["max_tokens"] == 256
    assert body["updated_at"] != original_updated_at


async def test_update_conversation_model(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user()
    model_a = await make_model()
    model_b = await make_model()
    created = await client.post("/api/conversations", json={"model": model_a.served_name})
    conv_id = created.json()["id"]

    patched = await client.patch(
        f"/api/conversations/{conv_id}", json={"model": model_b.served_name}
    )
    assert patched.status_code == 200
    assert patched.json()["model"] == model_b.served_name

    bad = await client.patch(f"/api/conversations/{conv_id}", json={"model": "no-such-model"})
    assert bad.status_code == 404


async def test_conversation_list_excludes_other_users(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user()
    model = await make_model()
    created = await client.post("/api/conversations", json={"model": model.served_name})
    conv_id = created.json()["id"]

    await login_as_new_user()  # 別ユーザーとしてログインし直す(同じclient)

    listed = await client.get("/api/conversations")
    assert listed.status_code == 200
    assert not any(c["id"] == conv_id for c in listed.json())


async def test_other_users_conversation_is_404(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user()
    model = await make_model()
    created = await client.post("/api/conversations", json={"model": model.served_name})
    conv_id = created.json()["id"]

    # 別ユーザーとしてログインし直す(同じclient、Cookieは上書きされる)
    await login_as_new_user()

    got = await client.get(f"/api/conversations/{conv_id}")
    assert got.status_code == 404

    deleted = await client.delete(f"/api/conversations/{conv_id}")
    assert deleted.status_code == 404


async def test_delete_conversation(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user()
    model = await make_model()
    created = await client.post("/api/conversations", json={"model": model.served_name})
    conv_id = created.json()["id"]

    deleted = await client.delete(f"/api/conversations/{conv_id}")
    assert deleted.status_code == 204

    got = await client.get(f"/api/conversations/{conv_id}")
    assert got.status_code == 404


async def test_send_message_without_model_400(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    await login_as_new_user()
    created = await client.post("/api/conversations", json={})
    conv_id = created.json()["id"]

    resp = await client.post(f"/api/conversations/{conv_id}/messages", json={"content": "hi"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "INVALID_REQUEST"


async def test_send_message_backend_payload_is_correct(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    fake_backend: FakeBackend,
) -> None:
    """served_name→backend_name変換、system_prompt、temperature/top_p/max_tokensが
    実際にバックエンドへ渡っていることを検証する。"""
    await login_as_new_user()
    model = await make_model()
    created = await client.post(
        "/api/conversations",
        json={
            "model": model.served_name,
            "system_prompt": "テスト用のsystem prompt",
            "temperature": 0.2,
            "top_p": 0.8,
            "max_tokens": 128,
        },
    )
    conv_id = created.json()["id"]

    async with client.stream(
        "POST", f"/api/conversations/{conv_id}/messages", json={"content": "1+1は？"}
    ) as resp:
        assert resp.status_code == 200
        async for _ in resp.aiter_bytes():
            pass

    assert len(fake_backend.chat_stream_calls) == 1
    sent = fake_backend.chat_stream_calls[0]
    assert sent["model"] == model.backend_name  # served_name ではなく backend_name
    assert sent["messages"] == [
        {"role": "system", "content": "テスト用のsystem prompt"},
        {"role": "user", "content": "1+1は？"},
    ]
    assert sent["temperature"] == 0.2
    assert sent["top_p"] == 0.8
    assert sent["max_tokens"] == 128
    assert sent["stream_options"] == {"include_usage": True}


async def test_send_message_multi_turn_includes_history(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    fake_backend: FakeBackend,
) -> None:
    await login_as_new_user()
    model = await make_model()
    created = await client.post("/api/conversations", json={"model": model.served_name})
    conv_id = created.json()["id"]

    for content in ("1回目", "2回目"):
        async with client.stream(
            "POST", f"/api/conversations/{conv_id}/messages", json={"content": content}
        ) as resp:
            async for _ in resp.aiter_bytes():
                pass

    assert len(fake_backend.chat_stream_calls) == 2
    second_call_messages = fake_backend.chat_stream_calls[1]["messages"]
    # 1回目のuser発言とassistant応答も履歴として含まれていること
    assert {"role": "user", "content": "1回目"} in second_call_messages
    assert {"role": "assistant", "content": "fake"} in second_call_messages
    assert second_call_messages[-1] == {"role": "user", "content": "2回目"}


async def test_send_message_multi_chunk_content_is_concatenated(
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
    created = await client.post("/api/conversations", json={"model": model.served_name})
    conv_id = created.json()["id"]

    async with client.stream(
        "POST", f"/api/conversations/{conv_id}/messages", json={"content": "hi"}
    ) as resp:
        async for _ in resp.aiter_bytes():
            pass

    messages = (await client.get(f"/api/conversations/{conv_id}/messages")).json()
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "fake reply"  # 最後のチャンクだけでなく全断片の連結


async def test_send_message_persists_history_and_usage_logs(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    db: AsyncSession,
) -> None:
    user = await login_as_new_user()
    model = await make_model()
    created = await client.post(
        "/api/conversations", json={"model": model.served_name, "system_prompt": "テスト用"}
    )
    conv_id = created.json()["id"]

    async with client.stream(
        "POST", f"/api/conversations/{conv_id}/messages", json={"content": "1+1は？"}
    ) as resp:
        assert resp.status_code == 200
        chunks = [c async for c in resp.aiter_bytes()]
    assert b"fake" in b"".join(chunks)

    messages = await client.get(f"/api/conversations/{conv_id}/messages")
    assert messages.status_code == 200
    roles = [m["role"] for m in messages.json()]
    assert roles == ["user", "assistant"]
    assert messages.json()[0]["content"] == "1+1は？"
    assert messages.json()[1]["content"] == "fake"

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "playground")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.status == "ok"
    assert log.model_id == model.id
    assert log.prompt_tokens == 5
    assert log.completion_tokens == 3
    assert log.ttft_ms is not None


async def test_send_message_other_users_conversation_404(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user()
    model = await make_model()
    created = await client.post("/api/conversations", json={"model": model.served_name})
    conv_id = created.json()["id"]

    await login_as_new_user()  # 別ユーザー

    resp = await client.post(f"/api/conversations/{conv_id}/messages", json={"content": "hi"})
    assert resp.status_code == 404
