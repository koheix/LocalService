"""管理API(/api/admin/*)のテスト。"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.backends.base import BackendHealth
from app.models import UsageLog, User
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


async def test_all_admin_mutation_and_list_endpoints_require_admin_role(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    """/health, /gpu 以外の /api/admin/* を全て網羅的に403確認する(HTTPレベル)。

    実際にリクエストを送り、403とエラーボディの形まで確認する。ただし
    このURL一覧は手動で維持しているため、新しいエンドポイントを追加して
    ここへの追記を忘れると検知できない(health/gpuは意図的な例外)。
    その「追記忘れ」自体を機械的に検知するのは
    test_all_admin_routes_declare_require_admin_dependency の役目。
    パスパラメータは実在しないID(999999999)を渡す。require_adminは
    エンドポイント本体(NotFoundError等)より先に評価されるため、
    admin以外なら404ではなく403になるはずである。
    """
    await login_as_new_user(role="user")

    checks: list[tuple[str, str, dict | None]] = [
        ("GET", "/api/admin/summary", None),
        ("GET", "/api/admin/usage?from=2024-01-01T00:00:00&to=2024-01-02T00:00:00", None),
        ("POST", "/api/admin/users", {"email": "x@example.local", "password": "x"}),
        ("PATCH", "/api/admin/users/999999999", {}),
        ("DELETE", "/api/admin/users/999999999", None),
        ("GET", "/api/admin/models", None),
        (
            "POST",
            "/api/admin/models",
            {"served_name": "x", "backend": "ollama", "backend_name": "x", "kind": "chat"},
        ),
        ("PATCH", "/api/admin/models/999999999", {}),
        ("DELETE", "/api/admin/models/999999999", None),
        ("POST", "/api/admin/models/999999999/pull", None),
    ]
    for method, url, json_body in checks:
        resp = await client.request(method, url, json=json_body)
        assert resp.status_code == 403, f"{method} {url} was {resp.status_code}, expected 403"
        assert resp.json()["error"]["code"] == "FORBIDDEN"


def _all_api_routes(routes: list) -> list:
    """FastAPIのapp.routesを再帰的に展開する。

    include_router()されたルートは`_IncludedRouter`(内部でoriginal_router
    属性に元のAPIRouterを保持する)でラップされ、app.routesを1階層見ただけ
    ではAPIRouteが取れないため、original_router.routesへ再帰する。
    """
    result: list = []
    for route in routes:
        if hasattr(route, "original_router"):
            result.extend(_all_api_routes(route.original_router.routes))
        elif hasattr(route, "path"):
            result.append(route)
    return result


def test_all_admin_routes_declare_require_admin_dependency() -> None:
    """/health, /gpu 以外の /api/admin/* が、実際にrequire_adminを

    dependencyとして宣言しているかをroute定義から直接検査する
    (HTTPリクエストは送らない)。T-20でルーター一括の
    dependencies=[Depends(require_admin)]を個別指定に変えたため、
    新しいエンドポイントを追加した際にこの指定を書き忘れても、
    上のtest_all_admin_mutation_and_list_endpoints_require_admin_roleの
    ような手動維持のURL一覧への追記を待たずに、ここで機械的に検知できる。

    確認済み: admin.pyの任意のエンドポイントから
    dependencies=[Depends(require_admin)]を外すと本テストが失敗する。
    """
    from app.deps import require_admin
    from app.main import app

    exempt_paths = {"/api/admin/health", "/api/admin/gpu"}
    admin_routes = [
        route
        for route in _all_api_routes(app.routes)
        if route.path.startswith("/api/admin") and route.path not in exempt_paths
    ]
    # ルート抽出自体が壊れて0件になった場合に「全部pass」を誤検知しないための下限。
    assert len(admin_routes) >= 10

    missing = [
        f"{sorted(route.methods)} {route.path}"
        for route in admin_routes
        if require_admin not in {dep.call for dep in route.dependant.dependencies}
    ]
    assert missing == [], f"require_adminが宣言されていないadminルート: {missing}"


async def test_admin_health(client: AsyncClient, login_as_new_user: Callable) -> None:
    await login_as_new_user(role="admin")
    resp = await client.get("/api/admin/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"llm", "embed", "db"}
    assert body["llm"]["ok"] is True
    assert body["embed"]["ok"] is True
    assert body["db"]["ok"] is True


async def test_admin_health_and_gpu_require_auth_401(client: AsyncClient) -> None:
    assert (await client.get("/api/admin/health")).status_code == 401
    assert (await client.get("/api/admin/gpu")).status_code == 401


async def test_admin_health_and_gpu_accessible_to_user_role(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    """docs/UI_HOME.md: ホーム画面のシステム状態パネルは一般ユーザーにも表示するため、

    /health と /gpu だけは admin 以外(user ロール)でも200になること。
    確認済み: admin.py の該当2エンドポイントから dependencies=[Depends(current_user)]
    を外す(無認証化する)と test_admin_health_and_gpu_require_auth_401 が失敗し、
    require_admin に戻すと本テストが403で失敗する。
    """
    await login_as_new_user(role="user")
    assert (await client.get("/api/admin/health")).status_code == 200
    assert (await client.get("/api/admin/gpu")).status_code == 200


async def test_admin_health_hides_backend_url_and_detail_from_user_role(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    """一般ユーザーにはホーム画面が使う ok/loaded_models だけを返し、

    backendのurlや例外detail(運用情報)はadmin限定のままにする。
    """
    await login_as_new_user(role="user")
    body = (await client.get("/api/admin/health")).json()
    assert set(body["llm"].keys()) == {"ok", "loaded_models"}
    assert set(body["embed"].keys()) == {"ok", "loaded_models"}
    assert set(body["db"].keys()) == {"ok"}


async def test_admin_summary_requires_admin_role(
    client: AsyncClient, login_as_new_user: Callable
) -> None:
    await login_as_new_user(role="user")
    resp = await client.get("/api/admin/summary")
    assert resp.status_code == 403


async def test_admin_summary_counts(
    client: AsyncClient,
    login_as_new_user: Callable,
    make_model: Callable,
    make_quota: Callable,
    db: AsyncSession,
    unique: Callable[[str], str],
) -> None:
    admin = await login_as_new_user(role="admin")

    before = await client.get("/api/admin/summary")
    assert before.status_code == 200
    base = before.json()

    # 有効なユーザーを1件追加(is_active=Trueが既定)。
    extra_user = User(
        email=unique("user") + "@example.local",
        display_name="",
        password_hash=hash_password("testpass123"),
        role="user",
    )
    db.add(extra_user)
    # 無効化されたモデルはカウントされないことも確認するため2件作る。
    enabled_model = await make_model(kind="chat")
    disabled_model = await make_model(kind="chat")
    disabled_model.is_enabled = False
    db.add(disabled_model)
    await db.commit()
    await db.refresh(enabled_model)

    db.add(
        UsageLog(
            user_id=admin.id,
            model_id=enabled_model.id,
            app="api",
            prompt_tokens=100,
            completion_tokens=23,
            status="ok",
        )
    )
    await db.commit()

    after = await client.get("/api/admin/summary")
    assert after.status_code == 200
    got = after.json()

    assert got["active_user_count"] == base["active_user_count"] + 1
    assert got["active_model_count"] == base["active_model_count"] + 1  # 無効化した分は増えない
    assert got["today_total_tokens"] == base["today_total_tokens"] + 123


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


async def test_admin_usage_group_by_day_uses_jst_boundary(
    client: AsyncClient, login_as_new_user: Callable, make_model: Callable, db: AsyncSession
) -> None:
    """日別集計はJST基準で区切ること(/api/admin/summaryの「今日」と同じ基準。

    T-28、D-017追記でユーザー確認済み)。UTC 23:30(=JST翌日08:30)のログが、
    UTC日付ではなくJST日付に区切られることを確認する。

    確認済み: admin.pyのgroup_by=="day"のkey_colを`func.date(UsageLog.created_at)`
    (JST変換なし)に戻すと、本テストで"2030-01-02"に集計されるはずのログが
    "2030-01-01"に集計されてしまい失敗する。
    """
    user = await login_as_new_user(role="admin")
    model = await make_model()

    db.add(
        UsageLog(
            user_id=user.id,
            model_id=model.id,
            app="api",
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1,
            status="ok",
            created_at=datetime(2030, 1, 1, 23, 30, tzinfo=UTC),  # JSTでは2030-01-02 08:30
        )
    )
    await db.commit()

    resp = await client.get(
        "/api/admin/usage",
        params={
            "from": "2020-01-01T00:00:00",
            "to": "2100-01-01T00:00:00",
            "group_by": "day",
            # 他のテスト(test_admin_usage_date_range_boundaries等)も2030年台の
            # 日付にusage_logsを作るため、user_idで自分の分だけに絞る
            # (絞らないと他テストのログが同じ日付バケットに混ざり得る)。
            "user_id": user.id,
        },
    )
    assert resp.status_code == 200
    days = {r["day"] for r in resp.json()["data"]}
    assert days == {"2030-01-02"}


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
