"""pytest 共通フィクスチャ。

前提: `make up && make migrate` 済みで postgres に到達できること
(`docker compose run` は同じ compose ネットワークに参加する)。
推論バックエンドは全テストで FakeBackend に差し替えるため、
Ollama が実際に起動している必要はない。

作成したユーザー/モデルは served_name/email に "pytest-" プレフィックスを
付け、セッション終了時にまとめて削除する。
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.backends.base import BackendHealth
from app.db import async_session_maker
from app.main import app
from app.models import Model, ModelPermission, User


class FakeBackend:
    """テスト用のバックエンド。実際の Ollama を呼ばない。"""

    async def list_models(self) -> list:
        return []

    async def chat(self, payload: dict) -> dict:
        return {
            "id": "fake",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "fake reply"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }

    async def chat_stream(self, payload: dict):
        yield b'data: {"choices":[{"delta":{"content":"fake"}}]}\n\n'
        usage = '{"prompt_tokens":5,"completion_tokens":3}'
        yield f'data: {{"choices":[{{"delta":{{}}}}],"usage":{usage}}}\n\n'.encode()
        yield b"data: [DONE]\n\n"

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        return [[0.0] * 1024 for _ in texts]

    async def health(self) -> BackendHealth:
        return BackendHealth(ok=True, url="fake://backend", loaded_models=["fake-model"])

    async def pull(self, model: str):
        yield {"status": "success"}


@pytest.fixture(autouse=True)
def _fake_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeBackend()
    for mod in ("app.routers.v1", "app.routers.conversations", "app.routers.admin"):
        monkeypatch.setattr(f"{mod}.get_chat_backend", lambda: fake)
    for mod in ("app.routers.v1", "app.routers.admin"):
        monkeypatch.setattr(f"{mod}.get_embed_backend", lambda: fake)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def new_client() -> Callable[[], AsyncClient]:
    """Cookie を共有しない新しい AsyncClient を作るファクトリ。

    Bearer 認証(APIキー)がセッションCookieに依存していないことを
    確認する際に使う。
    """

    def _make() -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")

    return _make


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session


@pytest.fixture
def unique() -> Callable[[str], str]:
    def _make(prefix: str) -> str:
        return f"pytest-{prefix}-{uuid.uuid4().hex[:8]}"

    return _make


@pytest_asyncio.fixture
async def login_as_new_user(
    client: AsyncClient, db: AsyncSession, unique: Callable[[str], str]
) -> Callable[..., Awaitable[User]]:
    async def _do(role: str = "user", password: str = "testpass123") -> User:
        email = unique("user") + "@example.local"
        user = User(email=email, display_name="", password_hash=hash_password(password), role=role)
        db.add(user)
        await db.commit()
        await db.refresh(user)

        resp = await client.post("/api/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200, resp.text
        return user

    return _do


@pytest_asyncio.fixture
async def make_model(
    db: AsyncSession, unique: Callable[[str], str]
) -> Callable[..., Awaitable[Model]]:
    async def _make(kind: str = "chat", roles: tuple[str, ...] = ("admin", "user")) -> Model:
        model = Model(
            served_name=unique("model"),
            backend="ollama" if kind == "chat" else "ollama-embed",
            backend_name="fake-backend-model",
            kind=kind,
        )
        db.add(model)
        await db.flush()
        for role in roles:
            db.add(ModelPermission(model_id=model.id, role=role))
        await db.commit()
        await db.refresh(model)
        return model

    return _make


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _cleanup_test_data() -> AsyncIterator[None]:
    yield
    async with async_session_maker() as session:
        await session.execute(delete(User).where(User.email.like("pytest-%")))
        await session.execute(delete(Model).where(Model.served_name.like("pytest-%")))
        await session.commit()
