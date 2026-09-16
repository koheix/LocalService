"""ユーザー管理・モデル管理・利用状況(adminロールのみ)。

ただし `/health` と `/gpu` のみ例外で、認証済みなら誰でも呼べる
(docs/UI_HOME.md: ホーム画面のシステム状態パネルは一般ユーザーにも表示し、
他の人が重い処理を流しているかを全員が判断できるようにするため。ユーザー確認済み)。
"""

import asyncio
import json
from datetime import UTC, date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.backends import get_chat_backend, get_embed_backend
from app.db import get_db
from app.deps import current_user, require_admin
from app.errors import ConflictError, NotFoundError
from app.models import Model, ModelPermission, UsageLog, User

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# health / gpu / usage
# ---------------------------------------------------------------------------


@router.get("/health")
async def health(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    llm_health = await get_chat_backend().health()
    embed_health = await get_embed_backend().health()

    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    if user.role == "admin":
        return {
            "llm": llm_health.model_dump(),
            "embed": embed_health.model_dump(),
            "db": {"ok": db_ok},
        }
    # 一般ユーザーにはホーム画面のシステム状態パネルが使う項目だけ返す。
    # backendのURLや例外詳細(detail)は運用情報のためadmin限定にする。
    return {
        "llm": {"ok": llm_health.ok, "loaded_models": llm_health.loaded_models},
        "embed": {"ok": embed_health.ok, "loaded_models": embed_health.loaded_models},
        "db": {"ok": db_ok},
    }


async def _query_gpu() -> dict[str, Any]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            return {"available": False}
        name, mem_total, mem_used, util, temp = (
            p.strip() for p in stdout.decode().strip().splitlines()[0].split(",")
        )
        return {
            "available": True,
            "name": name,
            "memory_total_mb": int(float(mem_total)),
            "memory_used_mb": int(float(mem_used)),
            "utilization_pct": int(float(util)),
            "temperature_c": int(float(temp)),
        }
    except (OSError, TimeoutError, IndexError, ValueError):
        return {"available": False}


@router.get("/gpu", dependencies=[Depends(current_user)])
async def gpu() -> dict[str, Any]:
    return await _query_gpu()


class SummaryOut(BaseModel):
    active_user_count: int
    active_model_count: int
    today_total_tokens: int


# 「当日」はJST基準(社内ツールで利用者が日本にいる前提。ユーザー確認済み)。
_JST = ZoneInfo("Asia/Tokyo")


@router.get("/summary", dependencies=[Depends(require_admin)])
async def summary(db: AsyncSession = Depends(get_db)) -> SummaryOut:
    """ホーム画面の管理セクション用に、件数を1回のリクエストでまとめて返す。"""
    active_user_count = await db.scalar(
        select(func.count()).select_from(User).where(User.is_active.is_(True))
    )
    active_model_count = await db.scalar(
        select(func.count()).select_from(Model).where(Model.is_enabled.is_(True))
    )
    today_start_jst = datetime.now(_JST).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start_jst.astimezone(UTC)
    today_tokens = await db.scalar(
        select(
            func.coalesce(func.sum(UsageLog.prompt_tokens + UsageLog.completion_tokens), 0)
        ).where(UsageLog.created_at >= today_start)
    )
    return SummaryOut(
        active_user_count=active_user_count or 0,
        active_model_count=active_model_count or 0,
        today_total_tokens=int(today_tokens or 0),
    )


@router.get("/usage", dependencies=[Depends(require_admin)])
async def usage(
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    user_id: int | None = Query(default=None),
    group_by: Literal["user", "model", "day"] = Query(default="day"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if group_by == "user":
        key_col = UsageLog.user_id.label("key")
    elif group_by == "model":
        key_col = UsageLog.model_id.label("key")
    else:
        # 「日」の区切りはJST基準にする(/api/admin/summaryの「今日」と同じ
        # 基準。ユーザー確認済み、D-017追記)。created_atはtimestamptzだが、
        # そのままfunc.date()に渡すとDBセッションのタイムゾーン設定に
        # 依存してしまうため、明示的にJSTへ変換してから日付を取り出す。
        key_col = func.date(func.timezone("Asia/Tokyo", UsageLog.created_at)).label("key")

    stmt = (
        select(
            key_col,
            func.count().label("request_count"),
            func.sum(UsageLog.prompt_tokens).label("prompt_tokens"),
            func.sum(UsageLog.completion_tokens).label("completion_tokens"),
        )
        .where(UsageLog.created_at >= date_from, UsageLog.created_at < date_to)
        .group_by(key_col)
        .order_by(key_col)
    )
    if user_id is not None:
        stmt = stmt.where(UsageLog.user_id == user_id)

    result = await db.execute(stmt)
    rows = [
        {
            group_by: row.key.isoformat() if isinstance(row.key, date) else row.key,
            "request_count": row.request_count,
            "prompt_tokens": int(row.prompt_tokens or 0),
            "completion_tokens": int(row.completion_tokens or 0),
        }
        for row in result
    ]
    return {"group_by": group_by, "data": rows}


# ---------------------------------------------------------------------------
# ユーザー管理
# ---------------------------------------------------------------------------


class UserCreateRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""
    role: Literal["admin", "user"] = "user"


class UserUpdateRequest(BaseModel):
    display_name: str | None = None
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None
    password: str | None = None


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    role: str
    is_active: bool
    created_at: datetime


def _user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        email=u.email,
        display_name=u.display_name,
        role=u.role,
        is_active=u.is_active,
        created_at=u.created_at,
    )


@router.get("/users", dependencies=[Depends(require_admin)])
async def list_users(db: AsyncSession = Depends(get_db)) -> list[UserOut]:
    result = await db.execute(select(User).order_by(User.id))
    return [_user_out(u) for u in result.scalars()]


@router.post("/users", status_code=201, dependencies=[Depends(require_admin)])
async def create_user(body: UserCreateRequest, db: AsyncSession = Depends(get_db)) -> UserOut:
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none() is not None:
        raise ConflictError(f"既に登録されているメールアドレスです: {body.email}")

    user = User(
        email=body.email,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _user_out(user)


@router.patch("/users/{user_id}", dependencies=[Depends(require_admin)])
async def update_user(
    user_id: int, body: UserUpdateRequest, db: AsyncSession = Depends(get_db)
) -> UserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError(f"ユーザーが見つかりません: {user_id}")

    if body.display_name is not None:
        user.display_name = body.display_name
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password is not None:
        user.password_hash = hash_password(body.password)

    await db.commit()
    await db.refresh(user)
    return _user_out(user)


@router.delete("/users/{user_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db)) -> None:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError(f"ユーザーが見つかりません: {user_id}")
    await db.delete(user)
    await db.commit()


# ---------------------------------------------------------------------------
# モデル管理
# ---------------------------------------------------------------------------


class ModelCreateRequest(BaseModel):
    served_name: str
    backend: str
    backend_name: str
    kind: Literal["chat", "embedding"]
    num_ctx: int = 4096
    description: str = ""
    permitted_roles: list[str] = ["admin", "user"]  # noqa: RUF012


class ModelUpdateRequest(BaseModel):
    backend_name: str | None = None
    num_ctx: int | None = None
    is_enabled: bool | None = None
    description: str | None = None
    permitted_roles: list[str] | None = None


class ModelOut(BaseModel):
    id: int
    served_name: str
    backend: str
    backend_name: str
    kind: str
    num_ctx: int
    is_enabled: bool
    description: str
    permitted_roles: list[str]


async def _permitted_roles(db: AsyncSession, model_id: int) -> list[str]:
    result = await db.execute(
        select(ModelPermission.role).where(ModelPermission.model_id == model_id)
    )
    return sorted(result.scalars().all())


async def _model_out(db: AsyncSession, m: Model) -> ModelOut:
    return ModelOut(
        id=m.id,
        served_name=m.served_name,
        backend=m.backend,
        backend_name=m.backend_name,
        kind=m.kind,
        num_ctx=m.num_ctx,
        is_enabled=m.is_enabled,
        description=m.description,
        permitted_roles=await _permitted_roles(db, m.id),
    )


@router.get("/models", dependencies=[Depends(require_admin)])
async def list_models(db: AsyncSession = Depends(get_db)) -> list[ModelOut]:
    result = await db.execute(select(Model).order_by(Model.id))
    return [await _model_out(db, m) for m in result.scalars()]


@router.post("/models", status_code=201, dependencies=[Depends(require_admin)])
async def create_model(body: ModelCreateRequest, db: AsyncSession = Depends(get_db)) -> ModelOut:
    result = await db.execute(select(Model).where(Model.served_name == body.served_name))
    if result.scalar_one_or_none() is not None:
        raise ConflictError(f"既に登録されているモデル名です: {body.served_name}")

    model = Model(
        served_name=body.served_name,
        backend=body.backend,
        backend_name=body.backend_name,
        kind=body.kind,
        num_ctx=body.num_ctx,
        description=body.description,
    )
    db.add(model)
    await db.flush()
    for role in body.permitted_roles:
        db.add(ModelPermission(model_id=model.id, role=role))
    await db.commit()
    await db.refresh(model)
    return await _model_out(db, model)


@router.patch("/models/{model_id}", dependencies=[Depends(require_admin)])
async def update_model(
    model_id: int, body: ModelUpdateRequest, db: AsyncSession = Depends(get_db)
) -> ModelOut:
    model = await db.get(Model, model_id)
    if model is None:
        raise NotFoundError(f"モデルが見つかりません: {model_id}")

    if body.backend_name is not None:
        model.backend_name = body.backend_name
    if body.num_ctx is not None:
        model.num_ctx = body.num_ctx
    if body.is_enabled is not None:
        model.is_enabled = body.is_enabled
    if body.description is not None:
        model.description = body.description
    if body.permitted_roles is not None:
        await db.execute(
            ModelPermission.__table__.delete().where(ModelPermission.model_id == model.id)
        )
        for role in body.permitted_roles:
            db.add(ModelPermission(model_id=model.id, role=role))

    await db.commit()
    await db.refresh(model)
    return await _model_out(db, model)


@router.delete("/models/{model_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_model(model_id: int, db: AsyncSession = Depends(get_db)) -> None:
    model = await db.get(Model, model_id)
    if model is None:
        raise NotFoundError(f"モデルが見つかりません: {model_id}")
    await db.delete(model)
    await db.commit()


@router.post("/models/{model_id}/pull", dependencies=[Depends(require_admin)])
async def pull_model(model_id: int, db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    model = await db.get(Model, model_id)
    if model is None:
        raise NotFoundError(f"モデルが見つかりません: {model_id}")

    backend = get_chat_backend() if model.kind == "chat" else get_embed_backend()
    backend_name = model.backend_name

    async def _progress() -> Any:
        async for event in backend.pull(backend_name):
            yield f"data: {json.dumps(event)}\n\n".encode()

    return StreamingResponse(_progress(), media_type="text/event-stream")
