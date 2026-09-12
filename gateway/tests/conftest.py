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
from app.backends.base import BackendHealth, InferenceBackend
from app.db import async_session_maker
from app.limits import concurrency_slots, rate_limiter
from app.main import app
from app.models import Document, Model, ModelPermission, Quota, User


class FakeBackend:
    """テスト用のバックエンド。実際の Ollama を呼ばない。

    受け取った payload を chat_calls/chat_stream_calls/embed_calls に
    記録するため、テスト側で「バックエンドに実際に何が送られたか」
    (served_name→backend_name変換、system_prompt、temperature等)を
    検証できる。chat_stream_chunks は差し替え可能にして、複数チャンクへの
    分割や usage 欠落などの異常系もテストできるようにしている。
    """

    def __init__(self) -> None:
        self.chat_calls: list[dict] = []
        self.chat_stream_calls: list[dict] = []
        self.embed_calls: list[tuple[list[str], str]] = []
        self.chat_stream_chunks: list[bytes] = [
            b'data: {"model":"fake-backend-model","choices":[{"delta":{"content":"fake"}}]}\n\n',
            b'data: {"model":"fake-backend-model","choices":[{"delta":{}}],'
            b'"usage":{"prompt_tokens":5,"completion_tokens":3}}\n\n',
            b"data: [DONE]\n\n",
        ]

    async def list_models(self) -> list:
        return []

    async def chat(self, payload: dict) -> dict:
        self.chat_calls.append(payload)
        return {
            "id": "fake",
            "object": "chat.completion",
            "model": payload.get("model", ""),
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
        self.chat_stream_calls.append(payload)
        for chunk in self.chat_stream_chunks:
            yield chunk

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        self.embed_calls.append((texts, model))
        return [[0.0] * 1024 for _ in texts]

    async def health(self) -> BackendHealth:
        return BackendHealth(ok=True, url="fake://backend", loaded_models=["fake-model"])

    async def pull(self, model: str):
        yield {"status": "success"}


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    fake = FakeBackend()
    assert isinstance(fake, InferenceBackend)

    chat_backend_mods = (
        "app.routers.v1",
        "app.routers.conversations",
        "app.routers.admin",
        "app.routers.rag",
    )
    for mod in chat_backend_mods:
        monkeypatch.setattr(f"{mod}.get_chat_backend", lambda: fake)
    for mod in ("app.routers.v1", "app.routers.admin", "app.routers.rag", "app.rag"):
        monkeypatch.setattr(f"{mod}.get_embed_backend", lambda: fake)

    # パッチが実際に効いていることをここで確認する。将来 import の形が
    # 変わってパッチが効かなくなった場合、テストが実Ollamaに到達して
    # タイムアウトする前にここで気づけるようにする。
    import app.rag as rag_mod
    import app.routers.admin as admin_mod
    import app.routers.conversations as conv_mod
    import app.routers.rag as rag_router_mod
    import app.routers.v1 as v1_mod

    assert v1_mod.get_chat_backend() is fake
    assert v1_mod.get_embed_backend() is fake
    assert conv_mod.get_chat_backend() is fake
    assert admin_mod.get_chat_backend() is fake
    assert admin_mod.get_embed_backend() is fake
    assert rag_router_mod.get_chat_backend() is fake
    assert rag_router_mod.get_embed_backend() is fake
    assert rag_mod.get_embed_backend() is fake

    return fake


@pytest.fixture(autouse=True)
def _reset_global_limits() -> None:
    """app.limits のシングルトンはプロセス共有なので、テスト間で状態を持ち越さない。"""
    rate_limiter._hits.clear()
    concurrency_slots._counts.clear()
    yield
    rate_limiter._hits.clear()
    concurrency_slots._counts.clear()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def new_client() -> Callable[[], AsyncClient]:
    """Cookie を共有しない新しい AsyncClient を作るファクトリ。

    Bearer 認証(APIキー)がセッションCookieに依存していないことや、
    サーバー側のセッション失効を確認する際に使う。
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


@pytest_asyncio.fixture
async def make_quota(db: AsyncSession) -> Callable[..., Awaitable[Quota]]:
    async def _make(user_id: int, *, rpm_limit: int = 60, max_concurrent: int = 2) -> Quota:
        quota = Quota(user_id=user_id, rpm_limit=rpm_limit, max_concurrent=max_concurrent)
        db.add(quota)
        await db.commit()
        return quota

    return _make


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_documents_after_test() -> AsyncIterator[None]:
    """テストが作った Document/Chunk を毎テスト後に消す(共有DBの chunks に

    残ると、RAGのコサイン距離検索が他テストの投入したベクトルまで拾って
    フレークする)。Chunk は documents への ondelete=CASCADE で連動削除される。
    ファイル名を "pytest-" プレフィックスで揃えている前提。
    """
    yield
    async with async_session_maker() as session:
        await session.execute(delete(Document).where(Document.filename.like("pytest-%")))
        await session.commit()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _cleanup_test_data() -> AsyncIterator[None]:
    yield
    async with async_session_maker() as session:
        await session.execute(delete(Document).where(Document.filename.like("pytest-%")))
        await session.execute(delete(User).where(User.email.like("pytest-%")))
        await session.execute(delete(Model).where(Model.served_name.like("pytest-%")))
        await session.commit()
