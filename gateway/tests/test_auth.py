"""認証(セッション/APIキー)のテスト。"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password, hash_session_token, new_session_token
from app.models import Session as SessionModel
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


async def test_garbage_session_cookie_401(new_client: Callable[[], AsyncClient]) -> None:
    async with new_client() as c2:
        c2.cookies.set("session", "this-is-not-a-real-token")
        resp = await c2.get("/api/auth/me")
        assert resp.status_code == 401


async def test_expired_session_401(
    db: AsyncSession, login_as_new_user: Callable, new_client: Callable[[], AsyncClient]
) -> None:
    user = await login_as_new_user()
    token = new_session_token()
    db.add(
        SessionModel(
            token_hash=hash_session_token(token),
            user_id=user.id,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    await db.commit()

    async with new_client() as c2:
        c2.cookies.set("session", token)
        resp = await c2.get("/api/auth/me")
        assert resp.status_code == 401


async def test_logout_invalidates_session_serverside(
    client: AsyncClient, login_as_new_user: Callable, new_client: Callable[[], AsyncClient]
) -> None:
    """Cookieクリアだけでなく、サーバー側でセッション行が削除されていることを確認する。
    盗まれた生トークンを別クライアントに直接セットして再利用できないことを見る。"""
    await login_as_new_user()
    raw_token = client.cookies.get("session")
    assert raw_token

    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 204

    async with new_client() as c2:
        c2.cookies.set("session", raw_token)
        resp = await c2.get("/api/auth/me")
        assert resp.status_code == 401


async def test_deactivated_user_is_locked_out(
    client: AsyncClient, login_as_new_user: Callable, db: AsyncSession
) -> None:
    user = await login_as_new_user()

    user.is_active = False
    await db.commit()

    me = await client.get("/api/auth/me")
    assert me.status_code == 401

    login2 = await client.post(
        "/api/auth/login", json={"email": user.email, "password": "testpass123"}
    )
    assert login2.status_code == 401


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


async def test_api_key_prefix_matches_but_secret_wrong_401(
    client: AsyncClient, login_as_new_user: Callable, new_client: Callable[[], AsyncClient]
) -> None:
    """prefixだけ当てても、本物の秘密部分が違えばArgon2検証で弾かれることを確認する。"""
    await login_as_new_user()
    created = await client.post("/api/auth/api-keys", json={"name": "k"})
    prefix = created.json()["prefix"]
    guessed_key = prefix + "0" * 40  # 同じprefixだが本物の続きではない

    async with new_client() as c2:
        resp = await c2.get("/api/auth/me", headers={"Authorization": f"Bearer {guessed_key}"})
        assert resp.status_code == 401


async def test_api_key_unknown_prefix_401(new_client: Callable[[], AsyncClient]) -> None:
    async with new_client() as c2:
        resp = await c2.get(
            "/api/auth/me", headers={"Authorization": "Bearer sk-doesnotexist000000"}
        )
        assert resp.status_code == 401


async def test_api_key_too_short_401(new_client: Callable[[], AsyncClient]) -> None:
    async with new_client() as c2:
        resp = await c2.get("/api/auth/me", headers={"Authorization": "Bearer short"})
        assert resp.status_code == 401


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


async def test_cannot_revoke_others_api_key(
    client: AsyncClient, login_as_new_user: Callable, new_client: Callable[[], AsyncClient]
) -> None:
    await login_as_new_user()
    created = await client.post("/api/auth/api-keys", json={"name": "a-key"})
    key_id = created.json()["id"]
    raw_key = created.json()["key"]

    await login_as_new_user()  # 別ユーザーとしてログインし直す(同じclient)

    resp = await client.delete(f"/api/auth/api-keys/{key_id}")
    assert resp.status_code == 404

    async with new_client() as c2:
        me = await c2.get("/api/auth/me", headers={"Authorization": f"Bearer {raw_key}"})
        assert me.status_code == 200
