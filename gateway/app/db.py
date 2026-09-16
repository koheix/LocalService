"""エンジン・セッション。"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

# セッションのタイムゾーンをUTCに固定する。
# routers/admin.pyのgroup_by=day集計(`func.timezone("Asia/Tokyo", ...)`で
# 明示的にJSTへ変換してからdate()を取る)自体はセッションTZに依存しない
# (実測済み: セッションTZをUTC/JST/その他どれにしても結果は変わらない)。
# それでもここで明示固定するのは、(1)将来func.date(timestamptz)のような
# 「セッションTZに暗黙依存する」SQL式を書いてしまった場合の保険、
# (2)group_by=dayのJST変換を検証する退行テストが、セッションTZがたまたま
# JST寄りになった場合に(暗黙のTZ変換と明示変換が一致してしまい)無自覚に
# 判定力を失うのを防ぐため(T-28レビューで発覚)。
engine: AsyncEngine = create_async_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    connect_args={"server_settings": {"timezone": "UTC"}},
)

async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
