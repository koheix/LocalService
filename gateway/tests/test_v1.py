"""OpenAI 互換エンドポイント(/api/v1/*)のテスト。バックエンドは FakeBackend。"""

from collections.abc import Callable

from httpx import AsyncClient


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
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user()
    model = await make_model()

    resp = await client.post(
        "/api/v1/chat/completions",
        json={"model": model.served_name, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "fake reply"


async def test_chat_completions_streaming(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
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


async def test_chat_completions_unknown_model_404(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    await login_as_new_user()
    resp = await client.post(
        "/api/v1/chat/completions",
        json={"model": "no-such-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 404


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


async def test_embeddings_success(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user()
    model = await make_model(kind="embedding")

    resp = await client.post(
        "/api/v1/embeddings", json={"model": model.served_name, "input": "hello"}
    )
    assert resp.status_code == 200
    assert len(resp.json()["data"][0]["embedding"]) == 1024


async def test_embeddings_wrong_kind_404(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user()
    chat_model = await make_model(kind="chat")

    resp = await client.post(
        "/api/v1/embeddings", json={"model": chat_model.served_name, "input": "hello"}
    )
    assert resp.status_code == 404
