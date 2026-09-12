"""会話(プレイグラウンド)のCRUD。自分の会話のみ操作できる。

他ユーザーの会話IDを指定した場合は、存在の有無を漏らさないため
403ではなく404を返す。
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user
from app.errors import NotFoundError
from app.models import Conversation, Message, Model, User

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationCreateRequest(BaseModel):
    title: str = ""
    model_id: int | None = None
    system_prompt: str = ""
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int | None = None


class ConversationUpdateRequest(BaseModel):
    title: str | None = None
    model_id: int | None = None
    system_prompt: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None


class ConversationOut(BaseModel):
    id: int
    title: str
    model_id: int | None
    system_prompt: str
    temperature: float
    top_p: float
    max_tokens: int | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime


def _conversation_out(c: Conversation) -> ConversationOut:
    return ConversationOut(
        id=c.id,
        title=c.title,
        model_id=c.model_id,
        system_prompt=c.system_prompt,
        temperature=c.temperature,
        top_p=c.top_p,
        max_tokens=c.max_tokens,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


async def _get_owned_conversation(
    db: AsyncSession, user: User, conversation_id: int
) -> Conversation:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise NotFoundError(f"会話が見つかりません: {conversation_id}")
    return conversation


async def _check_model_exists(db: AsyncSession, model_id: int | None) -> None:
    if model_id is None:
        return
    if await db.get(Model, model_id) is None:
        raise NotFoundError(f"モデルが見つかりません: {model_id}")


@router.get("")
async def list_conversations(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[ConversationOut]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
    )
    return [_conversation_out(c) for c in result.scalars()]


@router.post("", status_code=201)
async def create_conversation(
    body: ConversationCreateRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    await _check_model_exists(db, body.model_id)

    conversation = Conversation(
        user_id=user.id,
        title=body.title,
        model_id=body.model_id,
        system_prompt=body.system_prompt,
        temperature=body.temperature,
        top_p=body.top_p,
        max_tokens=body.max_tokens,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return _conversation_out(conversation)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    conversation = await _get_owned_conversation(db, user, conversation_id)
    return _conversation_out(conversation)


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: int,
    body: ConversationUpdateRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    conversation = await _get_owned_conversation(db, user, conversation_id)

    if body.model_id is not None:
        await _check_model_exists(db, body.model_id)
        conversation.model_id = body.model_id
    if body.title is not None:
        conversation.title = body.title
    if body.system_prompt is not None:
        conversation.system_prompt = body.system_prompt
    if body.temperature is not None:
        conversation.temperature = body.temperature
    if body.top_p is not None:
        conversation.top_p = body.top_p
    if body.max_tokens is not None:
        conversation.max_tokens = body.max_tokens
    conversation.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(conversation)
    return _conversation_out(conversation)


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    conversation = await _get_owned_conversation(db, user, conversation_id)
    await db.delete(conversation)
    await db.commit()


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    await _get_owned_conversation(db, user, conversation_id)
    result = await db.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
    )
    return [
        MessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at)
        for m in result.scalars()
    ]
