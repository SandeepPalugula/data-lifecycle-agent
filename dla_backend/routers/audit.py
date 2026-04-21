"""
routers/audit.py
Read-only access to the audit log for the dashboard.
"""
from datetime import datetime
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from ..database import get_db
from ..models import AuditLog, User
from ..auth import get_current_user

router = APIRouter(prefix="/audit", tags=["audit"])

class AuditEntry(BaseModel):
    id:              int
    event_type:      str
    actor_type:      str
    actor_id:        str | None
    conversation_id: str | None
    job_id:          str | None
    decision_id:     str | None
    detail:          dict
    created_at:      datetime

class PaginatedAudit(BaseModel):
    total: int
    page:  int
    size:  int
    items: list[AuditEntry]

@router.get("", response_model=PaginatedAudit)
async def list_audit(
    page:       int      = Query(1, ge=1),
    size:       int      = Query(50, ge=1, le=200),
    event_type: str | None = Query(None),
    db:         AsyncSession = Depends(get_db),
    user:       User     = Depends(get_current_user),
):
    q = select(AuditLog)
    if event_type:
        q = q.where(AuditLog.event_type == event_type)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar()
    items = (await db.execute(q.order_by(AuditLog.created_at.desc()).offset((page-1)*size).limit(size))).scalars().all()
    return PaginatedAudit(
        total=total, page=page, size=size,
        items=[AuditEntry(
            id=a.id, event_type=a.event_type.value, actor_type=a.actor_type.value,
            actor_id=str(a.actor_id) if a.actor_id else None,
            conversation_id=str(a.conversation_id) if a.conversation_id else None,
            job_id=str(a.job_id) if a.job_id else None,
            decision_id=str(a.decision_id) if a.decision_id else None,
            detail=a.detail, created_at=a.created_at,
        ) for a in items]
    )
