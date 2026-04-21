"""
routers/conversations.py
CRUD + analysis endpoints for conversations.
"""
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from ..database import get_db
from ..models import Conversation, ConversationState, SafetyFlag, Decision, User
from ..auth import get_current_user, require_analyst

router = APIRouter(prefix="/conversations", tags=["conversations"])

class ConversationIn(BaseModel):
    external_id:      str
    size_bytes:       int
    token_count:      int
    access_count:     int = 0
    attachment_bytes: int = 0

class ConversationOut(BaseModel):
    id:               str
    external_id:      str
    size_bytes:       int
    token_count:      int
    access_count:     int
    state:            str
    uniqueness_score: float | None
    utility_score:    float | None
    is_flagged:       bool
    last_accessed_at: datetime | None
    created_at:       datetime
    class Config:
        from_attributes = True

class PaginatedConversations(BaseModel):
    total:   int
    page:    int
    size:    int
    items:   list[ConversationOut]

@router.get("", response_model=PaginatedConversations)
async def list_conversations(
    page:     int   = Query(1, ge=1),
    size:     int   = Query(20, ge=1, le=100),
    state:    str | None = Query(None),
    db:       AsyncSession = Depends(get_db),
    user:     User = Depends(get_current_user),
):
    q = select(Conversation).where(Conversation.user_id == user.id)
    if state:
        q = q.where(Conversation.state == state)
    total_q = select(func.count()).select_from(q.subquery())
    total   = (await db.execute(total_q)).scalar()
    items   = (await db.execute(q.offset((page-1)*size).limit(size))).scalars().all()

    # Check flags
    flagged_ids = set()
    if items:
        ids = [c.id for c in items]
        flags = (await db.execute(select(SafetyFlag.conversation_id).where(SafetyFlag.conversation_id.in_(ids)))).scalars().all()
        flagged_ids = set(flags)

    return PaginatedConversations(
        total=total, page=page, size=size,
        items=[ConversationOut(
            id=str(c.id), external_id=c.external_id,
            size_bytes=c.size_bytes, token_count=c.token_count,
            access_count=c.access_count, state=c.state.value,
            uniqueness_score=c.uniqueness_score, utility_score=c.utility_score,
            is_flagged=c.id in flagged_ids,
            last_accessed_at=c.last_accessed_at, created_at=c.created_at,
        ) for c in items]
    )

@router.post("", response_model=ConversationOut, status_code=201)
async def register_conversation(
    payload: ConversationIn,
    db:      AsyncSession = Depends(get_db),
    user:    User = Depends(get_current_user),
):
    existing = await db.execute(select(Conversation).where(Conversation.external_id == payload.external_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Conversation already registered")
    conv = Conversation(
        user_id=user.id,
        external_id=payload.external_id,
        size_bytes=payload.size_bytes + payload.attachment_bytes,
        token_count=payload.token_count,
        access_count=payload.access_count,
    )
    db.add(conv)
    await db.flush()
    return ConversationOut(
        id=str(conv.id), external_id=conv.external_id,
        size_bytes=conv.size_bytes, token_count=conv.token_count,
        access_count=conv.access_count, state=conv.state.value,
        uniqueness_score=None, utility_score=None,
        is_flagged=False, last_accessed_at=None, created_at=conv.created_at,
    )

@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: UUID,
    db:  AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user.id)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    flag = (await db.execute(select(SafetyFlag).where(SafetyFlag.conversation_id == conversation_id))).scalar_one_or_none()
    return ConversationOut(
        id=str(conv.id), external_id=conv.external_id,
        size_bytes=conv.size_bytes, token_count=conv.token_count,
        access_count=conv.access_count, state=conv.state.value,
        uniqueness_score=conv.uniqueness_score, utility_score=conv.utility_score,
        is_flagged=flag is not None, last_accessed_at=conv.last_accessed_at,
        created_at=conv.created_at,
    )
