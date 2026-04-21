"""
workers/tasks.py

Celery task definitions. These are the actual units of work that
get dispatched to worker processes.

Why async_to_sync: Celery workers are synchronous by default but
our database layer uses async SQLAlchemy. We bridge this with
asyncio.run() so each task gets its own event loop.

Two tasks:
1. analyse_conversation — processes one job (called per conversation)
2. run_scheduled_batch  — the cron task that builds and dispatches jobs
"""

import asyncio
import uuid
from datetime import datetime
from celery import shared_task
from sqlalchemy import select

from ..workers.celery_app import celery_app
from ..database import AsyncSessionLocal
from ..models import (
    Conversation, ConversationState, AnalysisJob,
    SchedulerRun, SchedulerStatus, SchedulerTrigger,
    SafetyFlag, AuditEventType, AuditActorType
)
from ..services.decision_engine import run_analysis_job
from ..services.cost_oracle import get_current_costs
from ..audit import write_audit
from ..config import settings


# ── Task 1: Analyse one conversation ─────────────────────────

@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    queue="analysis",
    name="dla_backend.workers.tasks.analyse_conversation",
)
def analyse_conversation(self, job_id: str):
    """
    Process one analysis job.
    Called once per conversation per batch run.
    Retries up to 3 times with 60s delay if it fails.
    """
    try:
        result = asyncio.run(_async_analyse(uuid.UUID(job_id)))
        return result
    except Exception as exc:
        raise self.retry(exc=exc)


async def _async_analyse(job_id: uuid.UUID) -> dict:
    """Async wrapper — runs the full decision pipeline in a DB session."""
    async with AsyncSessionLocal() as db:
        try:
            result = await run_analysis_job(job_id, db)
            await db.commit()
            return result
        except Exception:
            await db.rollback()
            raise


# ── Task 2: Scheduled batch run ───────────────────────────────

@celery_app.task(
    name="dla_backend.workers.tasks.run_scheduled_batch",
    queue="scheduler",
)
def run_scheduled_batch(run_id: str | None = None):
    """
    The autonomous batch runner.
    Wakes up on schedule, checks conditions, builds job list,
    dispatches individual analyse_conversation tasks to the queue.

    Why check conditions here: the beat schedule fires every hour
    but we only want to actually run during off-peak hours. This
    task acts as its own gate.
    """
    return asyncio.run(_async_batch(run_id))


async def _async_batch(run_id: str | None) -> dict:
    """
    Full batch logic:
    1. Check system conditions (time of day, compute load)
    2. Find eligible conversations
    3. Create AnalysisJob rows
    4. Dispatch one Celery task per job
    5. Update SchedulerRun counters
    """
    async with AsyncSessionLocal() as db:
        try:
            # ── Check off-peak window ─────────────────────────
            hour = datetime.utcnow().hour
            is_off_peak = 1 <= hour <= 6

            costs = get_current_costs()
            compute_load = costs.get("compute_load_pct", 0)
            peak_factor  = float(costs["peak_factor"])

            # Load or create the scheduler run record
            if run_id:
                run_result = await db.execute(
                    select(SchedulerRun).where(SchedulerRun.id == uuid.UUID(run_id))
                )
                run = run_result.scalar_one_or_none()
            else:
                run = None

            if not run:
                run = SchedulerRun(
                    triggered_by=SchedulerTrigger.cron,
                    status=SchedulerStatus.running,
                    compute_load_pct=compute_load,
                    peak_factor=peak_factor,
                )
                db.add(run)
                await db.flush()

            # ── Stand-down checks ─────────────────────────────
            standdown_reason = None
            if not is_off_peak and run.triggered_by == SchedulerTrigger.cron:
                standdown_reason = f"Not off-peak hours (current hour: {hour} UTC)"
            elif compute_load > settings.AGENT_MAX_COMPUTE_LOAD_PCT:
                standdown_reason = f"Compute load {compute_load}% too high"
            elif peak_factor > settings.AGENT_PEAK_FACTOR_LIMIT:
                standdown_reason = f"Peak pricing {peak_factor}× too high"

            if standdown_reason:
                run.status = SchedulerStatus.standdown
                run.standdown_reason = standdown_reason
                run.completed_at = datetime.utcnow()
                await db.commit()
                return {"status": "standdown", "reason": standdown_reason}

            # ── Find eligible conversations ───────────────────
            # Eligible = active state, not safety flagged,
            # older than min age, not recently analysed
            from sqlalchemy import not_, exists
            from datetime import timedelta

            min_age_cutoff = datetime.utcnow() - timedelta(
                days=settings.MIN_CONVERSATION_AGE_DAYS
            )

            flagged_subq = select(SafetyFlag.conversation_id)

            eligible_q = (
                select(Conversation)
                .where(Conversation.state == ConversationState.active)
                .where(Conversation.created_at < min_age_cutoff)
                .where(Conversation.id.not_in(flagged_subq))
                .limit(settings.BATCH_SIZE_DEFAULT)
            )

            eligible_result = await db.execute(eligible_q)
            conversations = eligible_result.scalars().all()

            run.jobs_queued = len(conversations)

            await write_audit(
                db, AuditEventType.scheduler_run_started, AuditActorType.agent,
                {
                    "run_id": str(run.id),
                    "jobs_queued": len(conversations),
                    "hour_utc": hour,
                    "compute_load": compute_load,
                },
                scheduler_run_id=run.id,
            )

            if not conversations:
                run.status = SchedulerStatus.completed
                run.completed_at = datetime.utcnow()
                await db.commit()
                return {"status": "completed", "jobs_queued": 0}

            # ── Create jobs and dispatch tasks ────────────────
            job_ids = []
            for conv in conversations:
                job = AnalysisJob(
                    conversation_id=conv.id,
                    scheduler_run_id=run.id,
                )
                db.add(job)
                await db.flush()
                job_ids.append(str(job.id))

            await db.commit()

            # Dispatch one Celery task per job
            # These run in parallel across worker processes
            for job_id in job_ids:
                analyse_conversation.apply_async(
                    args=[job_id],
                    queue="analysis",
                )

            # Update run totals (approximate — actual counts
            # come in as workers complete)
            async with AsyncSessionLocal() as db2:
                run2_result = await db2.execute(
                    select(SchedulerRun).where(SchedulerRun.id == run.id)
                )
                run2 = run2_result.scalar_one_or_none()
                if run2:
                    run2.status = SchedulerStatus.completed
                    run2.jobs_processed = len(job_ids)
                    run2.completed_at = datetime.utcnow()
                    await db2.commit()

            return {
                "status": "completed",
                "jobs_queued": len(job_ids),
                "run_id": str(run.id),
            }

        except Exception as e:
            await db.rollback()
            if run:
                run.status = SchedulerStatus.aborted
                run.standdown_reason = str(e)
                run.completed_at = datetime.utcnow()
                try:
                    await db.commit()
                except Exception:
                    pass
            raise
