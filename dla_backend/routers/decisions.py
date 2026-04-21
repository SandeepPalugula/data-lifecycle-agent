"""
routers/decisions.py
List, confirm, and reject agent verdicts.
Forecasts computed on the fly using a single JOIN query — no N+1.
"""
from uuid import UUID
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from ..database import get_db
from ..models import Decision, Verdict, Conversation, User, AuditEventType, AuditActorType, utcnow
from ..auth import get_current_user, require_analyst
from ..audit import write_audit
from ..services.cost_oracle import get_current_costs
from ..services.forecaster import compute_decision_forecast, compute_batch_forecast

router = APIRouter(prefix="/decisions", tags=["decisions"])


class ForecastOut(BaseModel):
    verdict:            str
    monthly_saving_usd: float
    agent_cost_usd:     float
    break_even_months:  float
    forecast_3m_usd:    float
    forecast_6m_usd:    float
    forecast_12m_usd:   float
    compression_ratio:  float
    note:               str


class DecisionOut(BaseModel):
    id:                       str
    conversation_id:          str
    conversation_external_id: str
    conversation_size_bytes:  int
    conversation_age_days:    int
    verdict:                  str
    compression_strategy:     Optional[str]
    confidence_score:         Optional[float]  # ← R3
    storage_saving_usd:       float
    recompute_cost_usd:       float
    agent_cost_usd:           float
    net_saving_usd:           float
    uniqueness_score:         Optional[float]
    utility_score:            Optional[float]
    reasoning:                Optional[str]
    confirmation_required:    bool
    confirmed_at:             Optional[datetime]
    rejected_at:              Optional[datetime]
    created_at:               datetime
    forecast:                 Optional[ForecastOut]


class BatchForecastOut(BaseModel):
    actionable_count:     int
    total_monthly_usd:    float
    total_agent_cost_usd: float
    break_even_months:    float
    forecast_3m_usd:      float
    forecast_6m_usd:      float
    forecast_12m_usd:     float


def _build_forecast(
    decision: Decision,
    size_bytes: int,
    storage_cost_per_gb_day: float,
) -> ForecastOut:
    fc = compute_decision_forecast(
        verdict=decision.verdict.value,
        size_bytes=size_bytes,
        storage_cost_per_gb_day=storage_cost_per_gb_day,
        agent_cost_usd=float(decision.agent_cost_usd or 0),
    )
    return ForecastOut(
        verdict=fc.verdict,
        monthly_saving_usd=fc.monthly_saving_usd,
        agent_cost_usd=fc.agent_cost_usd,
        break_even_months=fc.break_even_months,
        forecast_3m_usd=fc.forecast_3m_usd,
        forecast_6m_usd=fc.forecast_6m_usd,
        forecast_12m_usd=fc.forecast_12m_usd,
        compression_ratio=fc.compression_ratio,
        note=fc.note,
    )


def _to_out(
    decision: Decision,
    size_bytes: int,
    external_id: str,
    age_days: int,
    storage_cost_per_gb_day: float,
) -> DecisionOut:
    forecast = _build_forecast(decision, size_bytes, storage_cost_per_gb_day)
    return DecisionOut(
        id=str(decision.id),
        conversation_id=str(decision.conversation_id),
        conversation_external_id=external_id,
        conversation_size_bytes=size_bytes,
        conversation_age_days=age_days,
        verdict=decision.verdict.value,
        compression_strategy=(
            decision.compression_strategy.value
            if decision.compression_strategy is not None
            else None
        ),
        confidence_score=(
            float(decision.confidence_score)
            if decision.confidence_score is not None
            else None
        ),
        storage_saving_usd=float(decision.storage_saving_usd or 0),
        recompute_cost_usd=float(decision.recompute_cost_usd or 0),
        agent_cost_usd=float(decision.agent_cost_usd or 0),
        net_saving_usd=float(decision.net_saving_usd or 0),
        uniqueness_score=float(decision.uniqueness_score) if decision.uniqueness_score is not None else None,
        utility_score=float(decision.utility_score) if decision.utility_score is not None else None,
        reasoning=decision.reasoning,
        confirmation_required=decision.confirmation_required,
        confirmed_at=decision.confirmed_at,
        rejected_at=decision.rejected_at,
        created_at=decision.created_at,
        forecast=forecast,
    )


@router.get("", response_model=list[DecisionOut])
async def list_decisions(
    pending_only: bool = False,
    db:   AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    costs = get_current_costs()
    storage_cost = float(costs["storage_cost_per_gb_day"])

    q = (
        select(Decision, Conversation.size_bytes,
               Conversation.external_id, Conversation.created_at)
        .join(Conversation, Decision.conversation_id == Conversation.id)
        .order_by(Decision.created_at.desc())
        .limit(50)
    )

    if pending_only:
        q = q.where(
            Decision.confirmation_required == True,
            Decision.confirmed_at == None,
            Decision.rejected_at == None,
        )

    result = await db.execute(q)
    rows = result.all()

    return [
        _to_out(
            decision=row[0],
            size_bytes=row[1],
            external_id=row[2],
            age_days=max((utcnow() - row[3]).days, 0),
            storage_cost_per_gb_day=storage_cost,
        )
        for row in rows
    ]


@router.get("/batch-forecast", response_model=BatchForecastOut)
async def get_batch_forecast(
    db:   AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    costs = get_current_costs()

    q = (
        select(Decision, Conversation.size_bytes)
        .join(Conversation, Decision.conversation_id == Conversation.id)
        .where(
            Decision.verdict.in_([Verdict.delete, Verdict.compress]),
            Decision.confirmed_at == None,
            Decision.rejected_at  == None,
        )
    )
    result = await db.execute(q)
    rows   = result.all()

    decisions_data = [
        {
            "verdict":        row[0].verdict.value,
            "size_bytes":     row[1],
            "agent_cost_usd": float(row[0].agent_cost_usd or 0),
        }
        for row in rows
    ]

    fc = compute_batch_forecast(
        decisions=decisions_data,
        storage_cost_per_gb_day=float(costs["storage_cost_per_gb_day"]),
    )

    return BatchForecastOut(
        actionable_count=fc.actionable_count,
        total_monthly_usd=fc.total_monthly_usd,
        total_agent_cost_usd=fc.total_agent_cost_usd,
        break_even_months=fc.break_even_months,
        forecast_3m_usd=fc.forecast_3m_usd,
        forecast_6m_usd=fc.forecast_6m_usd,
        forecast_12m_usd=fc.forecast_12m_usd,
    )


@router.post("/{decision_id}/confirm")
async def confirm_decision(
    decision_id: UUID,
    db:   AsyncSession = Depends(get_db),
    user: User = Depends(require_analyst),
):
    result = await db.execute(
        select(Decision).where(Decision.id == decision_id)
    )
    decision = result.scalar_one_or_none()
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")
    if not decision.confirmation_required:
        raise HTTPException(status_code=400, detail="Decision does not require confirmation")
    if decision.confirmed_at or decision.rejected_at:
        raise HTTPException(status_code=409, detail="Decision already actioned")

    decision.confirmed_at = utcnow()

    await write_audit(
        db, AuditEventType.confirmation_received, AuditActorType.user,
        {"action": "confirm", "decision_id": str(decision_id)},
        actor_id=user.id,
        decision_id=decision_id,
        conversation_id=decision.conversation_id,
    )
    await db.commit()
    return {"status": "confirmed"}


@router.post("/{decision_id}/reject")
async def reject_decision(
    decision_id: UUID,
    db:   AsyncSession = Depends(get_db),
    user: User = Depends(require_analyst),
):
    result = await db.execute(
        select(Decision).where(Decision.id == decision_id)
    )
    decision = result.scalar_one_or_none()
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")
    if decision.confirmed_at or decision.rejected_at:
        raise HTTPException(status_code=409, detail="Decision already actioned")

    decision.rejected_at = utcnow()

    await write_audit(
        db, AuditEventType.confirmation_received, AuditActorType.user,
        {"action": "reject", "decision_id": str(decision_id)},
        actor_id=user.id,
        decision_id=decision_id,
        conversation_id=decision.conversation_id,
    )
    await db.commit()
    return {"status": "rejected"}
