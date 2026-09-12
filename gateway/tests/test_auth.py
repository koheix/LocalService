"""認証(セッション/APIキー)のテスト。"""

from collections.abc import Callable

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.models import User


async def test_login_success_and_me(client: AsyncClient, login_as_new_user: Callable) -> None:
    user = await login_as_new_user()
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == user.email
    assert body["role"] == "user"


async def test_login_wrong_password_401(
    client: AsyncClient, db: AsyncSession, unique: Callable[[str], str]
) -> None:
    email = unique("user") + "@example.local"
    db.add(
        User(email=email, display_name="", password_hash=hash_password("correct-pw"), role="user")
    )
    await db.commit()

    resp = await client.post("/api/auth/login", json={"email": email, "password": "wrong-pw"})
    assert resp.status_code == 401


async def test_login_unknown_email_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/login", json={"email": "no-such-user@example.local", "password": "x"}
    )
    assert resp.status_code == 401


async def test_me_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_api_key_bearer_auth_and_last_used_at(
    client: AsyncClient, login_as_new_user: Callable, new_client: Callable[[], AsyncClient]
) -> None:
    user = await login_as_new_user()

    created = await client.post("/api/auth/api-keys", json={"name": "test-key"})
    assert created.status_code == 201
    body = created.json()
    assert body["key"].startswith("sk-")

    # Bearer 認証はセッションCookie無しでも通ること
    async with new_client() as c2:
        me = await c2.get("/api/auth/me", headers={"Authorization": f"Bearer {body['key']}"})
        assert me.status_code == 200
        assert me.json()["email"] == user.email

    keys = await client.get("/api/auth/api-keys")
    assert keys.status_code == 200
    assert keys.json()[0]["last_used_at"] is not None


async def test_logout_invalidates_session(client: AsyncClient, login_as_new_user: Callable) -> None:
    await login_as_new_user()
    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 204

    me = await client.get("/api/auth/me")
    assert me.status_code == 401


async def test_revoked_api_key_is_rejected(
    client: AsyncClient, login_as_new_user: Callable, new_client: Callable[[], AsyncClient]
) -> None:
    await login_as_new_user()
    created = await client.post("/api/auth/api-keys", json={"name": "to-revoke"})
    key_id = created.json()["id"]
    key = created.json()["key"]

    revoke = await client.delete(f"/api/auth/api-keys/{key_id}")
    assert revoke.status_code == 204

    async with new_client() as c2:
        me = await c2.get("/api/auth/me", headers={"Authorization": f"Bearer {key}"})
        assert me.status_code == 401
