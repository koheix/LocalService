"""管理API(/api/admin/*)のテスト。"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.backends.base import BackendHealth
from app.models import UsageLog
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


# ---------------------------------------------------------------------------
# GET /api/admin/usage
# ---------------------------------------------------------------------------


async def test_admin_usage_group_by_model_exact_counts(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    await login_as_new_user(role="admin")
    model = await make_model()

    for _ in range(2):
        resp = await client.post(
            "/api/v1/chat/completions",
            json={"model": model.served_name, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200

    listed = await client.get(
        "/api/admin/usage",
        params={"from": "2020-01-01T00:00:00", "to": "2100-01-01T00:00:00", "group_by": "model"},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["group_by"] == "model"
    row = next(r for r in body["data"] if r["model"] == model.id)
    assert row["request_count"] == 2
    assert row["prompt_tokens"] == 10  # FakeBackendは1回あたりprompt_tokens=5
    assert row["completion_tokens"] == 6  # 同completion_tokens=3


async def test_admin_usage_group_by_user_exact_counts(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    user = await login_as_new_user(role="admin")
    model = await make_model()
    resp = await client.post(
        "/api/v1/chat/completions",
        json={"model": model.served_name, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200

    listed = await client.get(
        "/api/admin/usage",
        params={"from": "2020-01-01T00:00:00", "to": "2100-01-01T00:00:00", "group_by": "user"},
    )
    assert listed.status_code == 200
    row = next(r for r in listed.json()["data"] if r["user"] == user.id)
    assert row["request_count"] == 1
    assert row["prompt_tokens"] == 5
    assert row["completion_tokens"] == 3


async def test_admin_usage_date_range_boundaries(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable, db: AsyncSession
) -> None:
    """from は含む(>=)、to は含まない(<)ことを、境界ちょうどの4件で厳密に確認する。

    from-1秒/from/to/to+1秒 の4件を仕込み、[from, to) の範囲では
    ちょうど1件(from の時刻のログ)だけがヒットするはず。
    >= が > になったり、< が <= になったりすると件数がずれて検出できる。
    """
    user = await login_as_new_user(role="admin")
    model = await make_model()

    base = datetime(2030, 1, 1, tzinfo=UTC)
    window = timedelta(seconds=10)

    def _log(offset: timedelta) -> UsageLog:
        return UsageLog(
            user_id=user.id,
            model_id=model.id,
            app="api",
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1,
            status="ok",
            created_at=base + offset,
        )

    db.add_all(
        [
            _log(-timedelta(seconds=1)),  # from の直前 → 範囲外
            _log(timedelta(0)),  # ちょうど from → 含まれる(>=)
            _log(window),  # ちょうど to → 含まれない(<)
            _log(window + timedelta(seconds=1)),  # to の直後 → 範囲外
        ]
    )
    await db.commit()

    resp = await client.get(
        "/api/admin/usage",
        params={
            "from": base.isoformat(),
            "to": (base + window).isoformat(),
            "group_by": "model",
        },
    )
    assert resp.status_code == 200
    matching = [r for r in resp.json()["data"] if r["model"] == model.id]
    assert len(matching) == 1
    assert matching[0]["request_count"] == 1


async def test_admin_usage_user_id_filter(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable
) -> None:
    user_a = await login_as_new_user(role="admin")
    model = await make_model()
    await client.post(
        "/api/v1/chat/completions",
        json={"model": model.served_name, "messages": [{"role": "user", "content": "hi"}]},
    )

    user_b = await login_as_new_user(role="admin")
    await client.post(
        "/api/v1/chat/completions",
        json={"model": model.served_name, "messages": [{"role": "user", "content": "hi"}]},
    )

    filtered = await client.get(
        "/api/admin/usage",
        params={
            "from": "2020-01-01T00:00:00",
            "to": "2100-01-01T00:00:00",
            "group_by": "user",
            "user_id": user_a.id,
        },
    )
    assert filtered.status_code == 200
    users_in_result = {r["user"] for r in filtered.json()["data"]}
    assert user_a.id in users_in_result
    assert user_b.id not in users_in_result
