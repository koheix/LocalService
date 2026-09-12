"""管理API(/api/admin/*)のテスト。"""

from collections.abc import Callable

import pytest
from httpx import AsyncClient

from app.backends.base import BackendHealth
from tests.conftest import FakeBackend


class _FakeProcess:
    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


async def test_admin_endpoints_require_auth_401(client: AsyncClient) -> None:
    """未認証は403ではなく401であること(認証と権限の区別)。"""
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 401


async def test_admin_endpoints_require_admin_role(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    await login_as_new_user(role="user")
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["type"] == "permission_error"
    assert body["error"]["code"] == "FORBIDDEN"


async def test_admin_health(client: AsyncClient, login_as_new_user: Callable) -> None:
    await login_as_new_user(role="admin")
    resp = await client.get("/api/admin/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"llm", "embed", "db"}
    assert body["llm"]["ok"] is True
    assert body["embed"]["ok"] is True
    assert body["db"]["ok"] is True


async def test_admin_health_reports_backend_down_without_500(
    client: AsyncClient, login_as_new_user: Callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    await login_as_new_user(role="admin")

    class DownBackend(FakeBackend):
        async def health(self) -> BackendHealth:
            return BackendHealth(ok=False, url="fake://down", detail="connection refused")

    monkeypatch.setattr("app.routers.admin.get_chat_backend", lambda: DownBackend())

    resp = await client.get("/api/admin/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm"]["ok"] is False
    assert body["llm"]["detail"] == "connection refused"


async def test_admin_gpu_success_path_exact_values(
    client: AsyncClient, login_as_new_user: Callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    await login_as_new_user(role="admin")

    async def fake_create_subprocess_exec(*args: object, **kwargs: object) -> _FakeProcess:
        return _FakeProcess(b"NVIDIA GeForce GTX 1080, 8192, 1024, 35, 55\n")

    monkeypatch.setattr(
        "app.routers.admin.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )

    resp = await client.get("/api/admin/gpu")
    assert resp.status_code == 200
    assert resp.json() == {
        "available": True,
        "name": "NVIDIA GeForce GTX 1080",
        "memory_total_mb": 8192,
        "memory_used_mb": 1024,
        "utilization_pct": 35,
        "temperature_c": 55,
    }


async def test_admin_gpu_missing_binary_returns_available_false(
    client: AsyncClient, login_as_new_user: Callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    await login_as_new_user(role="admin")

    async def fake_create_subprocess_exec(*args: object, **kwargs: object) -> _FakeProcess:
        raise FileNotFoundError("nvidia-smi not found")

    monkeypatch.setattr(
        "app.routers.admin.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )

    resp = await client.get("/api/admin/gpu")
    assert resp.status_code == 200
    assert resp.json() == {"available": False}


async def test_admin_gpu_nonzero_returncode_returns_available_false(
    client: AsyncClient, login_as_new_user: Callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    await login_as_new_user(role="admin")

    async def fake_create_subprocess_exec(*args: object, **kwargs: object) -> _FakeProcess:
        return _FakeProcess(b"", returncode=1)

    monkeypatch.setattr(
        "app.routers.admin.asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )

    resp = await client.get("/api/admin/gpu")
    assert resp.status_code == 200
    assert resp.json() == {"available": False}


async def test_create_user_response_shape_and_login_works(
    client: AsyncClient,
    login_as_new_user: Callable,
    unique: Callable[[str], str],
    new_client: Callable[[], AsyncClient],
) -> None:
    await login_as_new_user(role="admin")
    email = unique("newuser") + "@example.local"

    created = await client.post(
        "/api/admin/users", json={"email": email, "password": "testpass123"}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["email"] == email
    assert body["display_name"] == ""
    assert body["role"] == "user"
    assert body["is_active"] is True
    assert "password_hash" not in body
    assert "password" not in body

    async with new_client() as c2:
        login = await c2.post("/api/auth/login", json={"email": email, "password": "testpass123"})
        assert login.status_code == 200


async def test_duplicate_user_email_conflict_and_no_extra_row(
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
    assert dup.json()["error"]["code"] == "CONFLICT"

    listed = await client.get("/api/admin/users")
    matching = [u for u in listed.json() if u["email"] == email]
    assert len(matching) == 1


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
    payload = {
        "served_name": served_name,
        "backend": "ollama",
        "backend_name": "fake:1b",
        "kind": "chat",
    }

    created = await client.post("/api/admin/models", json=payload)
    assert created.status_code == 201
    assert set(created.json()["permitted_roles"]) == {"admin", "user"}

    dup = await client.post("/api/admin/models", json=payload)
    assert dup.status_code == 409

    listed = await client.get("/api/admin/models")
    matching = [m for m in listed.json() if m["served_name"] == served_name]
    assert len(matching) == 1


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
