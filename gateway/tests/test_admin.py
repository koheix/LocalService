"""管理API(/api/admin/*)のテスト。"""

from collections.abc import Callable

from httpx import AsyncClient


async def test_admin_endpoints_require_admin_role(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    await login_as_new_user(role="user")
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 403


async def test_admin_health(client: AsyncClient, login_as_new_user: Callable) -> None:
    await login_as_new_user(role="admin")
    resp = await client.get("/api/admin/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm"]["ok"] is True
    assert body["db"]["ok"] is True


async def test_admin_gpu_never_500(client: AsyncClient, login_as_new_user: Callable) -> None:
    await login_as_new_user(role="admin")
    resp = await client.get("/api/admin/gpu")
    assert resp.status_code == 200
    assert "available" in resp.json()


async def test_create_user_and_duplicate_email_conflict(
    client: AsyncClient, login_as_new_user: Callable, unique: Callable[[str], str]
) -> None:
    await login_as_new_user(role="admin")
    email = unique("newuser") + "@example.local"

    created = await client.post(
        "/api/admin/users", json={"email": email, "password": "testpass123"}
    )
    assert created.status_code == 201

    dup = await client.post("/api/admin/users", json={"email": email, "password": "testpass123"})
    assert dup.status_code == 409


async def test_update_and_delete_user(
    client: AsyncClient, login_as_new_user: Callable, unique: Callable[[str], str]
) -> None:
    await login_as_new_user(role="admin")
    email = unique("newuser") + "@example.local"
    created = await client.post(
        "/api/admin/users", json={"email": email, "password": "testpass123"}
    )
    user_id = created.json()["id"]

    patched = await client.patch(f"/api/admin/users/{user_id}", json={"is_active": False})
    assert patched.status_code == 200
    assert patched.json()["is_active"] is False

    deleted = await client.delete(f"/api/admin/users/{user_id}")
    assert deleted.status_code == 204

    missing = await client.delete(f"/api/admin/users/{user_id}")
    assert missing.status_code == 404


async def test_create_model_and_duplicate_served_name_conflict(
    client: AsyncClient, login_as_new_user: Callable, unique: Callable[[str], str]
) -> None:
    await login_as_new_user(role="admin")
    served_name = unique("model")

    created = await client.post(
        "/api/admin/models",
        json={
            "served_name": served_name,
            "backend": "ollama",
            "backend_name": "fake:1b",
            "kind": "chat",
        },
    )
    assert created.status_code == 201
    assert set(created.json()["permitted_roles"]) == {"admin", "user"}

    dup = await client.post(
        "/api/admin/models",
        json={
            "served_name": served_name,
            "backend": "ollama",
            "backend_name": "fake:1b",
            "kind": "chat",
        },
    )
    assert dup.status_code == 409


async def test_update_model_permitted_roles(
    client: AsyncClient, login_as_new_user: Callable, unique: Callable[[str], str]
) -> None:
    await login_as_new_user(role="admin")
    created = await client.post(
        "/api/admin/models",
        json={
            "served_name": unique("model"),
            "backend": "ollama",
            "backend_name": "fake:1b",
            "kind": "chat",
        },
    )
    model_id = created.json()["id"]

    patched = await client.patch(
        f"/api/admin/models/{model_id}", json={"permitted_roles": ["admin"]}
    )
    assert patched.status_code == 200
    assert patched.json()["permitted_roles"] == ["admin"]
