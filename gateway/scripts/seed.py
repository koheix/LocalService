"""初期データ投入。冪等（既存レコードがあればスキップ）。"""

import asyncio

from sqlalchemy import select

from app.auth import hash_password
from app.config import get_settings
from app.db import async_session_maker
from app.models import Model, ModelPermission, User

_MODEL_SPECS = [
    ("chat-standard", "ollama", "chat"),
    ("embed-standard", "ollama-embed", "embedding"),
]


async def _seed_admin(db) -> None:  # noqa: ANN001
    settings = get_settings()
    result = await db.execute(select(User).where(User.email == settings.admin_email))
    if result.scalar_one_or_none() is not None:
        return
    db.add(
        User(
            email=settings.admin_email,
            display_name="admin",
            password_hash=hash_password(settings.admin_initial_password),
            role="admin",
        )
    )


async def _seed_models(db) -> None:  # noqa: ANN001
    settings = get_settings()
    backend_name_by_kind = {
        "chat": settings.default_chat_model,
        "embedding": settings.default_embed_model,
    }
    for served_name, backend, kind in _MODEL_SPECS:
        result = await db.execute(select(Model).where(Model.served_name == served_name))
        model = result.scalar_one_or_none()
        if model is None:
            model = Model(
                served_name=served_name,
                backend=backend,
                backend_name=backend_name_by_kind[kind],
                kind=kind,
                num_ctx=settings.default_num_ctx,
            )
            db.add(model)
            await db.flush()

        for role in ("admin", "user"):
            result = await db.execute(
                select(ModelPermission).where(
                    ModelPermission.model_id == model.id, ModelPermission.role == role
                )
            )
            if result.scalar_one_or_none() is None:
                db.add(ModelPermission(model_id=model.id, role=role))


async def seed() -> None:
    async with async_session_maker() as db:
        await _seed_admin(db)
        await _seed_models(db)
        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed())
