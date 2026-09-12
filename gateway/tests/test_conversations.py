"""会話CRUDとチャット送信APIのテスト。バックエンドは FakeBackend。"""

from collections.abc import Callable

from httpx import AsyncClient


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


async def test_update_conversation_settings(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user()
    model = await make_model()
    created = await client.post("/api/conversations", json={"model": model.served_name})
    conv_id = created.json()["id"]

    patched = await client.patch(
        f"/api/conversations/{conv_id}",
        json={"system_prompt": "簡潔に答えて", "temperature": 0.2, "top_p": 0.8, "max_tokens": 256},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["system_prompt"] == "簡潔に答えて"
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 256


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


async def test_send_message_persists_history_and_usage_logs(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable, db
) -> None:
    from sqlalchemy import select

    from app.models import UsageLog

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
    assert messages.json()[1]["content"] == "fake"

    result = await db.execute(
        select(UsageLog).where(UsageLog.user_id == user.id, UsageLog.app == "playground")
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "ok"
