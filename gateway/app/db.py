"""エンジン・セッション。"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

# セッションのタイムゾーンをUTCに固定する。固定しないと(サーバー環境や
# initdbの設定次第で)DBセッションのタイムゾーンが変わり得て、
# func.date(timestamptz)のような「セッションTZに依存する」SQL式の結果が
# 環境ごとに変わってしまう(T-28で発覚。group_by=dayの日付集計をJSTに
# 明示変換する実装(routers/admin.py)は、この前提がある上で書かれている)。
engine: AsyncEngine = create_async_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    connect_args={"server_settings": {"timezone": "UTC"}},
)

async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
