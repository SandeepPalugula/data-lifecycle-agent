"""
routers/costs.py
Expose current and historical cost snapshots.
"""
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from ..database import get_db
from ..models import CostSnapshot, User
from ..auth import get_current_user

router = APIRouter(prefix="/costs", tags=["costs"])

class CostOut(BaseModel):
    id:                      str
    provider:                str
    storage_cost_per_gb_day: float
    compute_cost_per_ktok:   float
    peak_factor:             float
    captured_at:             datetime

@router.get("/latest", response_model=CostOut)
async def latest_cost(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(CostSnapshot).order_by(CostSnapshot.captured_at.desc()).limit(1))
    snap = result.scalar_one_or_none()
    if not snap:
        # Return sensible defaults if cost oracle hasn't run yet
        return CostOut(id="none", provider="default",
                       storage_cost_per_gb_day=0.023/1024,
                       compute_cost_per_ktok=0.003,
                       peak_factor=1.0, captured_at=datetime.utcnow())
    return CostOut(id=str(snap.id), provider=snap.provider,
                   storage_cost_per_gb_day=float(snap.storage_cost_per_gb_day),
                   compute_cost_per_ktok=float(snap.compute_cost_per_ktok),
                   peak_factor=float(snap.peak_factor), captured_at=snap.captured_at)

@router.get("/history", response_model=list[CostOut])
async def cost_history(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(CostSnapshot).order_by(CostSnapshot.captured_at.desc()).limit(100))
    snaps = result.scalars().all()
    return [CostOut(id=str(s.id), provider=s.provider,
                    storage_cost_per_gb_day=float(s.storage_cost_per_gb_day),
                    compute_cost_per_ktok=float(s.compute_cost_per_ktok),
                    peak_factor=float(s.peak_factor), captured_at=s.captured_at) for s in snaps]
