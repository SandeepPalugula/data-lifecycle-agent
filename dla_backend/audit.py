"""
audit.py
Single function for writing to the append-only audit log.
Every agent action, user action, and system event calls this.
"""

from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from .models import AuditLog, AuditEventType, AuditActorType

async def write_audit(
    db:              AsyncSession,
    event_type:      AuditEventType,
    actor_type:      AuditActorType,
    detail:          dict,
    actor_id:        UUID | None = None,
    conversation_id: UUID | None = None,
    job_id:          UUID | None = None,
    decision_id:     UUID | None = None,
    scheduler_run_id:UUID | None = None,
    ip_address:      str  | None = None,
    user_agent:      str  | None = None,
) -> AuditLog:
    """
    Write one audit event. Called after every significant action.
    The audit_log table is insert-only — this function never updates
    or deletes existing rows.
    """
    entry = AuditLog(
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        conversation_id=conversation_id,
        job_id=job_id,
        decision_id=decision_id,
        scheduler_run_id=scheduler_run_id,
        detail=detail,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(entry)
    await db.flush()   # gets the id without full commit
    return entry
