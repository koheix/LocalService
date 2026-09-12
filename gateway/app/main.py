"""FastAPI アプリ生成、ルーター登録。"""

import logging

import structlog
from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.errors import register_error_handlers
from app.routers import admin, auth, conversations, documents, v1


def _configure_logging() -> None:
    """config.LOG_LEVEL を structlog/標準logging双方に反映する。"""
    level = get_settings().log_level.upper()
    logging.basicConfig(format="%(message)s", level=level)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.KeyValueRenderer(key_order=["event"]),
        ],
    )


def create_app() -> FastAPI:
    _configure_logging()
    app = FastAPI(
        title="llm-console gateway",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    register_error_handlers(app)
    app.include_router(auth.router)
    app.include_router(v1.router)
    app.include_router(admin.router)
    app.include_router(conversations.router)
    app.include_router(documents.router)

    @app.get("/api/health")
    async def health(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
        await db.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


app = create_app()
