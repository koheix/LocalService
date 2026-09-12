"""FastAPI アプリ生成、ルーター登録。"""

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.errors import register_error_handlers
from app.routers import auth


def create_app() -> FastAPI:
    app = FastAPI(title="llm-console gateway")
    register_error_handlers(app)
    app.include_router(auth.router)

    @app.get("/api/health")
    async def health(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
        await db.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


app = create_app()
