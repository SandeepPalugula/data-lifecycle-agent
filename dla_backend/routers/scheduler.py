"""
routers/scheduler.py
5-level batch pipeline with:
  - Metadata summary generation (Level 1a)
  - Heuristic pre-screen using recency-weighted access score (Level 1b)
  - Conversation clustering (Level 1c)
  - Cumulative batch gate on clusters (Level 2)
  - API scoring of cluster representatives in parallel (Level 3) ← P1
  - Verdict applied to all cluster members with proportional cost (Level 4)
  - Cumulative summary (Level 5)

P2: Semantic score caching.
P3: Adaptive batch sizing.
P4: Incremental runs.
P5: Compression quality scoring.
R1: Idempotent job recovery.
R2: Decision expiry and re-evaluation.
R3: Confidence scores on verdicts.
R4: Rollback window enforcement.
R5: Anomaly alerting — after each run, three signals are checked:
    1. Cost spike: agent cost > 3× average of last 5 runs
    2. Negative ROI batch: net saving < -$0.10
    3. Deletion surge: >50% of processed jobs were DELETE verdicts
    Each triggered anomaly prints a [R5 ANOMALY] warning. In production
    this is where email/Slack/PagerDuty alerts would be sent.
"""
import asyncio
from uuid import UUID, uuid4
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, update, func
from pydantic import BaseModel
from ..database import get_db, AsyncSessionLocal
from ..models import (
    SchedulerRun, SchedulerTrigger, SchedulerStatus,
    Conversation, ConversationState,
    AnalysisJob, JobStatus, Decision, Verdict,
    CompressionStrategy, Compression,
    SafetyFlag, User,
    AuditEventType, AuditActorType
)
from ..auth import require_analyst, get_current_user
from ..audit import write_audit
from ..services.cost_oracle import get_current_costs
from ..services.scorer import score_conversation
from ..services.summarizer import (
    generate_summary,
    compute_weighted_access_score,
    weighted_score_label,
    WSCORE_HIGH, WSCORE_MEDIUM, WSCORE_LOW, WSCORE_COLD,
)
from ..services.clusterer import (
    cluster_conversations, ConversationCluster,
    format_cluster_verdict_reasoning
)
from ..config import settings

router = APIRouter(prefix="/scheduler", tags=["scheduler"])

# ── Heuristic thresholds ──────────────────────────────────────
LIKELY_KEEP_ACCESS_DAYS    = 7
LIKELY_KEEP_MAX_BYTES      = 10_000
STRONG_CANDIDATE_MIN_DAYS  = 90
STRONG_CANDIDATE_MIN_BYTES = 1_000_000

# ── P3: Adaptive batch sizing bounds ─────────────────────────
# TODO: promote to .env once production data shows what bounds work well


# ── R5: Anomaly detection thresholds ─────────────────────────
ANOMALY_COST_SPIKE_MULTIPLIER = 3.0    # agent cost > 3× recent average
ANOMALY_NEGATIVE_ROI_USD      = -0.10  # net saving worse than -$0.10
ANOMALY_DELETION_SURGE_PCT    = 0.50   # >50% of jobs are deletes


def now() -> datetime:
    return datetime.now(timezone.utc)


def make_aware(dt: datetime) -> datetime:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _adaptive_batch_size(costs: dict) -> int:
    """P3: Compute adaptive batch size from cost ratio."""
    storage_cost = float(costs.get("storage_cost_per_gb_day", 0.0001))
    compute_cost = float(costs.get("compute_cost_per_ktok", 0.01))

    if compute_cost <= 0:
        return settings.BATCH_SIZE_DEFAULT

    ratio      = storage_cost / compute_cost
    RATIO_HIGH = 0.01
    RATIO_LOW  = 0.001

    if ratio >= RATIO_HIGH:
        return settings.BATCH_SIZE_MAX
    elif ratio <= RATIO_LOW:
        return settings.BATCH_SIZE_MIN
    else:
        t    = (ratio - RATIO_LOW) / (RATIO_HIGH - RATIO_LOW)
        size = settings.BATCH_SIZE_MIN + t * (settings.BATCH_SIZE_MAX - settings.BATCH_SIZE_MIN)
        return int(round(size))


def _score_is_fresh(conv: Conversation) -> bool:
    """P2: Return True if the conversation has a cached score within TTL."""
    if (conv.uniqueness_score is None
            or conv.utility_score is None
            or conv.last_scored_at is None):
        return False
    last_scored = make_aware(conv.last_scored_at)
    ttl         = timedelta(days=settings.SCORER_CACHE_TTL_DAYS)
    return (now() - last_scored) < ttl


def _select_compression_strategy(
    uniqueness: float,
    utility: float,
) -> CompressionStrategy:
    """P5: Select compression strategy from scores."""
    HIGH = 0.5
    if uniqueness >= HIGH and utility >= HIGH:
        return CompressionStrategy.qa
    elif uniqueness >= HIGH and utility < HIGH:
        return CompressionStrategy.keypoints
    else:
        return CompressionStrategy.summary


def _compute_confidence(
    uniqueness: float,
    utility: float,
    net_saving: float,
    agent_cost: float,
    actual_tokens: int,
) -> float:
    """R3: Compute confidence score 0.0-1.0."""
    if actual_tokens > 0:
        score_quality = 1.0
    elif uniqueness == 0.5 and utility == 0.5:
        score_quality = 0.3
    else:
        score_quality = 0.7

    u_distance      = abs(uniqueness - 0.5) * 2
    v_distance      = abs(utility    - 0.5) * 2
    signal_strength = (u_distance + v_distance) / 2

    if agent_cost > 0:
        clarity = min(abs(net_saving) / (agent_cost * 10), 1.0)
    elif net_saving != 0:
        clarity = 1.0
    else:
        clarity = 0.0

    confidence = (score_quality + signal_strength + clarity) / 3
    return round(min(max(confidence, 0.0), 1.0), 4)


async def _recover_orphaned_jobs(run_id: str):
    """R1: Mark orphaned running jobs as failed and requeue their conversations."""
    async with AsyncSessionLocal() as db:
        try:
            orphaned_result = await db.execute(
                select(AnalysisJob)
                .where(AnalysisJob.status == JobStatus.running)
                .where(
                    or_(
                        AnalysisJob.scheduler_run_id == None,
                        AnalysisJob.scheduler_run_id != UUID(run_id),
                    )
                )
            )
            orphaned_jobs = orphaned_result.scalars().all()

            if not orphaned_jobs:
                return

            orphaned_conv_ids = [job.conversation_id for job in orphaned_jobs]

            for job in orphaned_jobs:
                job.status        = JobStatus.failed
                job.error_message = "Orphaned by server restart or aborted run — requeued by R1 recovery."
                job.completed_at  = now()

            await db.execute(
                update(Conversation)
                .where(Conversation.id.in_(orphaned_conv_ids))
                .values(last_scored_at=None)
            )

            await db.commit()
            print(f"[R1] Recovered {len(orphaned_jobs)} orphaned job(s) — "
                  f"conversations requeued for this run.")

        except Exception as e:
            await db.rollback()
            print(f"[R1] ERROR during orphaned job recovery: {e}")


async def _expire_stale_decisions():
    """R2: Expire pending decisions past their confirmation window."""
    async with AsyncSessionLocal() as db:
        try:
            expired_result = await db.execute(
                select(Decision)
                .where(Decision.confirmation_required == True)
                .where(Decision.confirmed_at == None)
                .where(Decision.rejected_at == None)
                .where(Decision.confirmation_expires_at != None)
                .where(Decision.confirmation_expires_at < now())
            )
            expired_decisions = expired_result.scalars().all()

            if not expired_decisions:
                return

            expired_conv_ids = [d.conversation_id for d in expired_decisions]

            for decision in expired_decisions:
                decision.rejected_at = now()
                decision.reasoning   = (
                    (decision.reasoning or "") +
                    f"\n\n[R2] Decision expired at {decision.confirmation_expires_at} "
                    f"without confirmation. Conversation requeued for re-evaluation."
                )

            await db.execute(
                update(Conversation)
                .where(Conversation.id.in_(expired_conv_ids))
                .values(last_scored_at=None)
            )

            await db.commit()
            print(f"[R2] Expired {len(expired_decisions)} stale decision(s) — "
                  f"conversations requeued for re-evaluation.")

        except Exception as e:
            await db.rollback()
            print(f"[R2] ERROR during decision expiry: {e}")


async def _enforce_rollback_windows():
    """R4: Permanently commit compressions whose rollback window has elapsed."""
    async with AsyncSessionLocal() as db:
        try:
            expired_result = await db.execute(
                select(Compression)
                .where(Compression.original_preserved_until != None)
                .where(Compression.original_preserved_until < now())
                .where(Compression.rolled_back_at == None)
            )
            expired_compressions = expired_result.scalars().all()

            if not expired_compressions:
                return

            for compression in expired_compressions:
                preserved_until = compression.original_preserved_until
                compression.original_preserved_until = None

                await write_audit(
                    db, AuditEventType.compression_executed, AuditActorType.agent,
                    {
                        "event":                    "rollback_window_expired",
                        "compression_id":           str(compression.id),
                        "original_preserved_until": str(preserved_until),
                        "note": (
                            "Rollback window elapsed without rollback request. "
                            "Original content permanently committed. "
                            "In production: original deleted from storage."
                        ),
                    },
                    conversation_id=compression.conversation_id,
                    decision_id=compression.decision_id,
                )

            await db.commit()
            print(f"[R4] Committed {len(expired_compressions)} compression(s) — "
                  f"rollback windows elapsed, originals permanently committed.")

        except Exception as e:
            await db.rollback()
            print(f"[R4] ERROR during rollback window enforcement: {e}")


async def _check_anomalies(
    run_id: str,
    jobs_processed: int,
    jobs_deleted: int,
    agent_cost: float,
    net_saving: float,
):
    """
    R5: Anomaly alerting.

    Checks three signals after each completed run and prints a warning
    for any that are triggered. In production, replace the print statements
    with email / Slack / PagerDuty calls.

    Signals:
    1. Cost spike    — agent cost > ANOMALY_COST_SPIKE_MULTIPLIER × recent average
    2. Negative ROI  — net saving < ANOMALY_NEGATIVE_ROI_USD
    3. Deletion surge — deleted jobs > ANOMALY_DELETION_SURGE_PCT of processed
    """
    try:
        async with AsyncSessionLocal() as db:
            # Fetch last 5 completed runs (excluding current) for cost baseline
            recent_result = await db.execute(
                select(SchedulerRun.agent_cost_usd)
                .where(SchedulerRun.status == SchedulerStatus.completed)
                .where(SchedulerRun.id != UUID(run_id))
                .order_by(SchedulerRun.started_at.desc())
                .limit(5)
            )
            recent_costs = [float(r[0]) for r in recent_result.fetchall()]

        # ── Signal 1: Cost spike ──────────────────────────────
        if recent_costs:
            avg_cost = sum(recent_costs) / len(recent_costs)
            if avg_cost > 0 and agent_cost > avg_cost * ANOMALY_COST_SPIKE_MULTIPLIER:
                print(
                    f"[R5 ANOMALY] Cost spike detected — "
                    f"agent cost ${agent_cost:.5f} is "
                    f"{agent_cost / avg_cost:.1f}× the recent average "
                    f"(${avg_cost:.5f} over last {len(recent_costs)} run(s)). "
                    f"Run: {run_id}"
                )

        # ── Signal 2: Negative ROI ────────────────────────────
        if net_saving < ANOMALY_NEGATIVE_ROI_USD:
            print(
                f"[R5 ANOMALY] Negative ROI batch — "
                f"net saving ${net_saving:.5f} is below threshold "
                f"${ANOMALY_NEGATIVE_ROI_USD:.2f}. "
                f"Check standdown settings or decision rules. "
                f"Run: {run_id}"
            )

        # ── Signal 3: Deletion surge ──────────────────────────
        if jobs_processed > 0:
            deletion_pct = jobs_deleted / jobs_processed
            if deletion_pct > ANOMALY_DELETION_SURGE_PCT:
                print(
                    f"[R5 ANOMALY] Deletion surge — "
                    f"{jobs_deleted}/{jobs_processed} jobs "
                    f"({deletion_pct*100:.0f}%) were DELETE verdicts, "
                    f"exceeding the {ANOMALY_DELETION_SURGE_PCT*100:.0f}% threshold. "
                    f"Verify decision rules and test data. "
                    f"Run: {run_id}"
                )

    except Exception as e:
        print(f"[R5] ERROR during anomaly check: {e}")


def heuristic_screen(conv: Conversation, summary: str) -> tuple[str, str]:
    """Level 1b: Cheap pre-screen using metadata + weighted access score."""
    created_at    = make_aware(conv.created_at)
    last_accessed = make_aware(conv.last_accessed_at)
    age_days      = max((now() - created_at).days, 0)
    wscore        = compute_weighted_access_score(conv)
    eng_label     = weighted_score_label(wscore, conv.access_count)

    if wscore >= WSCORE_HIGH:
        return "likely_keep", (
            f"{summary}\n\nPre-screen: KEEP — weighted access score {wscore:.2f} "
            f"(threshold ≥{WSCORE_HIGH}). {eng_label}. No API scoring needed."
        )

    if last_accessed:
        days_since = (now() - last_accessed).days
        if days_since < LIKELY_KEEP_ACCESS_DAYS:
            return "likely_keep", (
                f"{summary}\n\nPre-screen: KEEP — last accessed {days_since} day(s) ago "
                f"(threshold <{LIKELY_KEEP_ACCESS_DAYS} days). "
                f"Weighted score: {wscore:.2f}. No API scoring needed."
            )

    if conv.size_bytes < LIKELY_KEEP_MAX_BYTES:
        return "likely_keep", (
            f"{summary}\n\nPre-screen: KEEP — {conv.size_bytes} bytes is below "
            f"the {LIKELY_KEEP_MAX_BYTES}-byte minimum for meaningful savings."
        )

    if wscore == 0.0 and age_days > STRONG_CANDIDATE_MIN_DAYS:
        return "strong_candidate", (
            f"{summary}\n\nPre-screen: STRONG CANDIDATE — "
            f"weighted score {wscore:.2f} (never accessed), "
            f"{age_days} days old. High priority for analysis."
        )

    if conv.size_bytes > STRONG_CANDIDATE_MIN_BYTES and wscore < WSCORE_COLD:
        return "strong_candidate", (
            f"{summary}\n\nPre-screen: STRONG CANDIDATE — "
            f"{conv.size_bytes/1024/1024:.1f}MB with very low weighted "
            f"engagement ({wscore:.2f}). High priority for analysis."
        )

    return "candidate", (
        f"{summary}\n\nPre-screen: CANDIDATE — {age_days} days old, "
        f"weighted access score {wscore:.2f} ({eng_label}), "
        f"{conv.size_bytes/1024:.1f}KB. Forwarding to clustering."
    )


def apply_decision_rules(
    age_days: int,
    wscore: float,
    access_count: int,
    uniqueness: float,
    utility: float,
    net_saving: float,
) -> tuple[Verdict, bool]:
    """Apply decision rules. Returns (verdict, confirmation_required)."""
    if net_saving < 0:
        return Verdict.keep, False
    if wscore >= WSCORE_MEDIUM:
        return Verdict.keep, False
    if (wscore < WSCORE_COLD and uniqueness < 0.2
            and utility < 0.2 and age_days > 30):
        return Verdict.delete, True
    if (wscore < WSCORE_LOW or age_days > 180) and net_saving > 0:
        return Verdict.compress, True
    return Verdict.keep, False


class RunOut(BaseModel):
    id:              str
    status:          str
    triggered_by:    str
    jobs_queued:     int
    jobs_processed:  int
    jobs_deleted:    int
    jobs_compressed: int
    total_saving_usd:float
    agent_cost_usd:  float
    net_saving_usd:  float
    started_at:      datetime
    completed_at:    datetime | None

class TriggerResponse(BaseModel):
    run_id:  str
    message: str


@router.post("/run", response_model=TriggerResponse)
async def trigger_run(
    background_tasks: BackgroundTasks,
    db:   AsyncSession = Depends(get_db),
    user: User = Depends(require_analyst),
):
    active = await db.execute(
        select(SchedulerRun).where(SchedulerRun.status == SchedulerStatus.running)
    )
    if active.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A batch run is already in progress")

    run = SchedulerRun(
        triggered_by=SchedulerTrigger.manual,
        triggered_by_user=user.id,
        status=SchedulerStatus.running,
    )
    db.add(run)
    await db.flush()
    run_id = run.id

    await write_audit(
        db, AuditEventType.scheduler_run_started, AuditActorType.user,
        {"trigger": "manual", "triggered_by": str(user.id)},
        actor_id=user.id, scheduler_run_id=run_id,
    )

    background_tasks.add_task(_run_pipeline, str(run_id))

    return TriggerResponse(
        run_id=str(run_id),
        message=f"Batch run started. Poll /scheduler/runs/{run_id} for status."
    )


@router.get("/runs", response_model=list[RunOut])
async def list_runs(
    db:   AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(SchedulerRun).order_by(SchedulerRun.started_at.desc()).limit(50)
    )
    return [_to_out(r) for r in result.scalars().all()]


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(
    run_id: UUID,
    db:     AsyncSession = Depends(get_db),
    user:   User = Depends(get_current_user),
):
    result = await db.execute(
        select(SchedulerRun).where(SchedulerRun.id == run_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _to_out(run)


def _to_out(r: SchedulerRun) -> RunOut:
    return RunOut(
        id=str(r.id), status=r.status.value,
        triggered_by=r.triggered_by.value,
        jobs_queued=r.jobs_queued, jobs_processed=r.jobs_processed,
        jobs_deleted=r.jobs_deleted, jobs_compressed=r.jobs_compressed,
        total_saving_usd=float(r.total_saving_usd),
        agent_cost_usd=float(r.agent_cost_usd),
        net_saving_usd=float(r.net_saving_usd),
        started_at=r.started_at, completed_at=r.completed_at,
    )


async def _run_pipeline(run_id: str):
    try:
        costs      = get_current_costs()
        batch_size = _adaptive_batch_size(costs)

        await _recover_orphaned_jobs(run_id)   # R1
        await _expire_stale_decisions()         # R2
        await _enforce_rollback_windows()       # R4

        async with AsyncSessionLocal() as db:
            last_run_result = await db.execute(
                select(SchedulerRun.started_at)
                .where(SchedulerRun.status == SchedulerStatus.completed)
                .where(SchedulerRun.id != UUID(run_id))
                .order_by(SchedulerRun.started_at.desc())
                .limit(1)
            )
            last_run_started_at = last_run_result.scalar_one_or_none()

            stale_cutoff = now() - timedelta(days=settings.SCORER_CACHE_TTL_DAYS)
            incremental_conditions = [
                Conversation.last_scored_at == None,
                Conversation.last_scored_at < stale_cutoff,
            ]
            if last_run_started_at is not None:
                last_run_started_at = make_aware(last_run_started_at)
                incremental_conditions.append(
                    Conversation.created_at > last_run_started_at
                )

            flagged = [
                r[0] for r in (
                    await db.execute(select(SafetyFlag.conversation_id))
                ).fetchall()
            ]
            min_age = now() - timedelta(days=settings.MIN_CONVERSATION_AGE_DAYS)
            q = (
                select(Conversation)
                .where(Conversation.state == ConversationState.active)
                .where(Conversation.created_at < min_age)
                .where(or_(*incremental_conditions))
                .limit(batch_size)
            )
            if flagged:
                q = q.where(Conversation.id.not_in(flagged))
            conversations = (await db.execute(q)).scalars().all()

        total_eligible = len(conversations)
        async with AsyncSessionLocal() as db:
            run = (await db.execute(
                select(SchedulerRun).where(SchedulerRun.id == UUID(run_id))
            )).scalar_one_or_none()
            if run:
                run.jobs_queued = total_eligible
                await db.commit()

        if not total_eligible:
            await _complete_run(run_id, 0, 0, 0, 0, 0, 0)
            await _check_anomalies(run_id, 0, 0, 0.0, 0.0)  # R5
            return

        conv_summaries = {conv.id: generate_summary(conv) for conv in conversations}

        likely_keeps = []
        candidates   = []
        for conv in conversations:
            summary  = conv_summaries[conv.id]
            category, reasoning = heuristic_screen(conv, summary)
            if category == "likely_keep":
                likely_keeps.append((conv, reasoning))
            else:
                candidates.append((conv, category, reasoning))

        for conv, reasoning in likely_keeps:
            await _write_heuristic_keep(conv, run_id, reasoning, costs)

        if not candidates:
            await _complete_run(
                run_id, processed=len(likely_keeps),
                deleted=0, compressed=0, saving=0, agent_cost=0, net=0,
            )
            await _check_anomalies(run_id, len(likely_keeps), 0, 0.0, 0.0)  # R5
            return

        candidate_convs = [conv for conv, _, _ in candidates]
        clusters = cluster_conversations(candidate_convs, conv_summaries)

        total_candidate_saving     = 0.0
        total_candidate_agent_cost = 0.0
        cluster_estimates          = {}

        for cluster in clusters:
            cluster_saving = sum(
                (conv.size_bytes / 1024 / 1024 / 1024)
                * float(costs["storage_cost_per_gb_day"]) * 30
                for conv in cluster.members
            )
            rep        = cluster.representative
            rep_tokens = settings.AGENT_TOKENS_PER_CALL
            rep_cost   = (rep_tokens / 1000) * float(costs["compute_cost_per_ktok"]) * 1.12

            cluster_estimates[cluster.key] = {
                "storage_saving": cluster_saving,
                "rep_tokens":     rep_tokens,
                "rep_cost":       rep_cost,
            }
            total_candidate_saving     += cluster_saving
            total_candidate_agent_cost += rep_cost

        if not settings.SKIP_STANDDOWN_CHECK:
            if (total_candidate_saving <= 0 or
                    total_candidate_agent_cost > total_candidate_saving):

                cluster_text = "\n".join(
                    f"  - {c.summary} (1 API call covers {c.size} conversation(s))"
                    for c in clusters
                )
                reason = (
                    f"Batch gate: {len(clusters)} cluster(s) covering "
                    f"{len(candidate_convs)} conversation(s).\n"
                    f"Combined storage saving: ${total_candidate_saving:.6f}\n"
                    f"Combined agent cost:     ${total_candidate_agent_cost:.6f}\n"
                    f"Agent cost exceeds savings — batch stood down.\n\n"
                    f"Clusters:\n{cluster_text}\n\n"
                    f"Note: {len(likely_keeps)} conversation(s) kept via "
                    f"heuristic pre-screen at zero cost."
                )
                for cluster in clusters:
                    for conv in cluster.members:
                        await _write_standdown_record(conv, run_id, reason[:500], costs)
                await _complete_run(
                    run_id,
                    processed=len(likely_keeps) + len(candidate_convs),
                    deleted=0, compressed=0,
                    saving=0, agent_cost=total_candidate_agent_cost, net=0,
                )
                await _check_anomalies(  # R5
                    run_id,
                    jobs_processed=len(likely_keeps) + len(candidate_convs),
                    jobs_deleted=0,
                    agent_cost=total_candidate_agent_cost,
                    net_saving=0.0,
                )
                return

        raw_results = await asyncio.gather(
            *[
                _score_cluster(
                    cluster=cluster,
                    run_id=UUID(run_id),
                    costs=costs,
                    estimates=cluster_estimates[cluster.key],
                    conv_summaries=conv_summaries,
                )
                for cluster in clusters
            ],
            return_exceptions=True,
        )

        jobs_deleted     = 0
        jobs_compressed  = 0
        total_saving     = 0.0
        total_agent_cost = 0.0

        for i, result in enumerate(raw_results):
            if isinstance(result, Exception):
                print(f"ERROR in parallel cluster scoring (cluster {i}): {result}")
                continue
            jobs_deleted     += result.get("deleted",        0)
            jobs_compressed  += result.get("compressed",     0)
            total_saving     += result.get("net_saving_usd", 0)
            total_agent_cost += result.get("agent_cost_usd", 0)

        total_processed = len(likely_keeps) + len(candidate_convs)
        net             = total_saving - total_agent_cost

        await _complete_run(
            run_id,
            processed=total_processed,
            deleted=jobs_deleted, compressed=jobs_compressed,
            saving=total_saving, agent_cost=total_agent_cost,
            net=net,
        )

        # R5: Check for anomalies after the run is recorded
        await _check_anomalies(
            run_id=run_id,
            jobs_processed=total_processed,
            jobs_deleted=jobs_deleted,
            agent_cost=total_agent_cost,
            net_saving=net,
        )

    except Exception as e:
        print(f"ERROR in _run_pipeline: {e}")
        await _abort_run(run_id, str(e))


async def _score_cluster(
    cluster: ConversationCluster,
    run_id: UUID,
    costs: dict,
    estimates: dict,
    conv_summaries: dict,
) -> dict:
    rep         = cluster.representative
    rep_summary = conv_summaries.get(rep.id, "")
    rep_wscore  = compute_weighted_access_score(rep)

    member_savings: dict = {}
    total_cluster_saving = 0.0
    for member in cluster.members:
        size_gb = member.size_bytes / 1024 / 1024 / 1024
        saving  = size_gb * float(costs["storage_cost_per_gb_day"]) * 30
        member_savings[member.id] = saving
        total_cluster_saving += saving

    async with AsyncSessionLocal() as db:
        try:
            rep_job = AnalysisJob(
                conversation_id=rep.id,
                scheduler_run_id=run_id,
                status=JobStatus.running,
                started_at=now(),
            )
            db.add(rep_job)
            await db.flush()

            created_at = make_aware(rep.created_at)
            age_days   = max((now() - created_at).days, 0)

            if _score_is_fresh(rep):
                uniqueness         = float(rep.uniqueness_score)
                utility            = float(rep.utility_score)
                actual_tokens      = 0
                full_rep_reasoning = (
                    f"Scores served from cache (last scored: "
                    f"{rep.last_scored_at.date()}, "
                    f"TTL: {settings.SCORER_CACHE_TTL_DAYS} days).\n\n"
                    f"[Cluster: {cluster.summary}]\n"
                    f"[Weighted access score: {rep_wscore:.2f}]\n"
                    f"[Summary: {rep_summary.split(chr(10))[0]}]"
                )
            else:
                conv_description = (
                    f"Conversation analysis request (cluster representative):\n\n"
                    f"CLUSTER CONTEXT:\n{cluster.summary}\n"
                    f"This conversation represents {cluster.size} similar conversation(s).\n\n"
                    f"METADATA SUMMARY:\n{rep_summary}\n\n"
                    f"RAW METADATA:\n"
                    f"- Age: {age_days} days\n"
                    f"- Size: {rep.size_bytes} bytes ({rep.size_bytes/1024:.1f}KB)\n"
                    f"- Token count: {rep.token_count:,}\n"
                    f"- Access count: {rep.access_count} "
                    f"(weighted score: {rep_wscore:.2f})\n"
                    f"- Last accessed: {rep.last_accessed_at or 'never'}\n\n"
                    f"Please score this conversation. Your verdict will apply to "
                    f"all {cluster.size} conversation(s) in this cluster."
                )
                try:
                    scores        = await score_conversation(conv_description)
                    uniqueness    = float(scores.get("uniqueness_score", 0.5))
                    utility       = float(scores.get("utility_value", 0.5))
                    api_reasoning = scores.get("reasoning", "No reasoning provided.")
                    actual_tokens = (
                        scores.get("input_tokens", 0) + scores.get("output_tokens", 0)
                    ) or estimates["rep_tokens"]
                    full_rep_reasoning = (
                        f"{api_reasoning}\n\n"
                        f"[Cluster: {cluster.summary}]\n"
                        f"[Weighted access score: {rep_wscore:.2f}]\n"
                        f"[Summary: {rep_summary.split(chr(10))[0]}]"
                    )
                except Exception as e:
                    uniqueness         = 0.5
                    utility            = 0.5
                    api_reasoning      = f"API scoring unavailable ({e}). Defaulting to KEEP."
                    full_rep_reasoning = api_reasoning
                    actual_tokens      = estimates["rep_tokens"]

                rep.uniqueness_score = uniqueness
                rep.utility_score    = utility
                rep.last_scored_at   = now()

            actual_agent_cost = (
                actual_tokens / 1000
            ) * float(costs["compute_cost_per_ktok"]) * 1.12

            rep_saving      = member_savings[rep.id]
            rep_agent_share = (
                actual_agent_cost * (rep_saving / total_cluster_saving)
                if total_cluster_saving > 0
                else actual_agent_cost
            )
            rep_recompute  = (
                uniqueness * (rep.token_count / 1000)
                * float(costs["compute_cost_per_ktok"]) * 0.4
            )
            rep_net_saving = rep_saving - rep_recompute - rep_agent_share

            verdict, needs_confirmation = apply_decision_rules(
                age_days=age_days,
                wscore=rep_wscore,
                access_count=rep.access_count,
                uniqueness=uniqueness,
                utility=utility,
                net_saving=rep_net_saving,
            )

            compression_strategy = (
                _select_compression_strategy(uniqueness, utility)
                if verdict == Verdict.compress else None
            )

            confidence = _compute_confidence(
                uniqueness=uniqueness,
                utility=utility,
                net_saving=rep_net_saving,
                agent_cost=rep_agent_share,
                actual_tokens=actual_tokens,
            )

            rep_decision = Decision(
                job_id=rep_job.id, conversation_id=rep.id,
                verdict=verdict,
                compression_strategy=compression_strategy,
                confidence_score=confidence,
                storage_saving_usd=rep_saving,
                recompute_cost_usd=rep_recompute,
                agent_cost_usd=rep_agent_share,
                net_saving_usd=rep_net_saving,
                uniqueness_score=uniqueness, utility_score=utility,
                reasoning=full_rep_reasoning,
                confirmation_required=needs_confirmation,
                confirmation_token=str(uuid4()) if needs_confirmation else None,
                confirmation_expires_at=now() + timedelta(hours=24) if needs_confirmation else None,
            )
            db.add(rep_decision)

            rep_job.status            = JobStatus.completed
            rep_job.agent_tokens_used = actual_tokens
            rep_job.agent_cost_usd    = rep_agent_share
            rep_job.completed_at      = now()

            await write_audit(
                db, AuditEventType.verdict_issued, AuditActorType.agent,
                {
                    "verdict":              verdict.value,
                    "compression_strategy": compression_strategy.value if compression_strategy else None,
                    "confidence_score":     confidence,
                    "method":               "api_scoring_representative",
                    "cluster_key":          cluster.key,
                    "cluster_size":         cluster.size,
                    "api_calls_saved":      cluster.size - 1,
                    "rep_weighted_score":   round(rep_wscore, 4),
                    "cache_hit":            actual_tokens == 0,
                },
                job_id=rep_job.id, conversation_id=rep.id,
                decision_id=rep_decision.id,
            )
            await db.commit()

        except Exception as e:
            await db.rollback()
            print(f"ERROR scoring representative {rep.id}: {e}")
            return {"deleted": 0, "compressed": 0,
                    "net_saving_usd": 0, "agent_cost_usd": 0}

    jobs_deleted    = 1 if verdict == Verdict.delete   else 0
    jobs_compressed = 1 if verdict == Verdict.compress else 0
    total_net       = rep_net_saving

    for member in cluster.non_representatives:
        member_saving      = member_savings[member.id]
        member_agent_share = (
            actual_agent_cost * (member_saving / total_cluster_saving)
            if total_cluster_saving > 0 else 0.0
        )
        result = await _write_cluster_member_decision(
            conv=member,
            run_id=run_id,
            costs=costs,
            cluster=cluster,
            verdict=verdict,
            compression_strategy=compression_strategy,
            confidence_score=confidence,
            representative_reasoning=full_rep_reasoning,
            representative_id=str(rep.id),
            uniqueness=uniqueness,
            utility=utility,
            storage_saving=member_saving,
            agent_cost_share=member_agent_share,
        )
        if result.get("verdict") == Verdict.delete.value:   jobs_deleted    += 1
        if result.get("verdict") == Verdict.compress.value: jobs_compressed += 1
        total_net += result.get("net_saving_usd", 0)

    return {
        "deleted":        jobs_deleted,
        "compressed":     jobs_compressed,
        "net_saving_usd": total_net,
        "agent_cost_usd": actual_agent_cost,
    }


async def _write_cluster_member_decision(
    conv, run_id, costs, cluster,
    verdict, compression_strategy, confidence_score,
    representative_reasoning, representative_id,
    uniqueness, utility, storage_saving, agent_cost_share,
) -> dict:
    async with AsyncSessionLocal() as db:
        try:
            job = AnalysisJob(
                conversation_id=conv.id,
                scheduler_run_id=run_id,
                status=JobStatus.completed,
                started_at=now(), completed_at=now(),
                agent_tokens_used=0,
                agent_cost_usd=agent_cost_share,
            )
            db.add(job)
            await db.flush()

            recompute_cost = (
                uniqueness * (conv.token_count / 1000)
                * float(costs["compute_cost_per_ktok"]) * 0.4
            )
            net_saving         = storage_saving - recompute_cost - agent_cost_share
            needs_confirmation = verdict in (Verdict.delete, Verdict.compress)
            member_wscore      = compute_weighted_access_score(conv)

            member_reasoning = (
                format_cluster_verdict_reasoning(
                    cluster=cluster,
                    representative_reasoning=representative_reasoning,
                    representative_id=representative_id,
                ) +
                f"\n\nThis member's weighted access score: {member_wscore:.2f}. "
                f"Agent cost share: ${agent_cost_share:.6f} "
                f"(proportional to storage saving fraction)."
            )

            decision = Decision(
                job_id=job.id, conversation_id=conv.id,
                verdict=verdict,
                compression_strategy=compression_strategy,
                confidence_score=confidence_score,
                storage_saving_usd=storage_saving,
                recompute_cost_usd=recompute_cost,
                agent_cost_usd=agent_cost_share,
                net_saving_usd=net_saving,
                uniqueness_score=uniqueness,
                utility_score=utility,
                reasoning=member_reasoning,
                confirmation_required=needs_confirmation,
                confirmation_token=str(uuid4()) if needs_confirmation else None,
                confirmation_expires_at=now() + timedelta(hours=24) if needs_confirmation else None,
            )
            db.add(decision)

            await write_audit(
                db, AuditEventType.verdict_issued, AuditActorType.agent,
                {
                    "verdict":               verdict.value,
                    "compression_strategy":  compression_strategy.value if compression_strategy else None,
                    "confidence_score":      confidence_score,
                    "method":                "cluster_member",
                    "cluster_key":           cluster.key,
                    "member_weighted_score": round(member_wscore, 4),
                    "agent_cost_share":      round(agent_cost_share, 8),
                },
                job_id=job.id, conversation_id=conv.id, decision_id=decision.id,
            )
            await db.commit()
            return {"verdict": verdict.value, "net_saving_usd": net_saving}

        except Exception as e:
            await db.rollback()
            print(f"ERROR writing cluster member decision {conv.id}: {e}")
            return {"verdict": "failed", "net_saving_usd": 0}


async def _write_heuristic_keep(conv, run_id, reasoning, costs):
    async with AsyncSessionLocal() as db:
        try:
            job = AnalysisJob(
                conversation_id=conv.id,
                scheduler_run_id=UUID(run_id),
                status=JobStatus.completed,
                started_at=now(), completed_at=now(),
                agent_tokens_used=0, agent_cost_usd=0,
            )
            db.add(job)
            await db.flush()

            size_gb        = conv.size_bytes / 1024 / 1024 / 1024
            storage_saving = size_gb * float(costs["storage_cost_per_gb_day"]) * 30

            decision = Decision(
                job_id=job.id, conversation_id=conv.id,
                verdict=Verdict.keep,
                compression_strategy=None,
                confidence_score=None,
                storage_saving_usd=storage_saving,
                recompute_cost_usd=0, agent_cost_usd=0, net_saving_usd=0,
                reasoning=reasoning, confirmation_required=False,
            )
            db.add(decision)
            await write_audit(
                db, AuditEventType.verdict_issued, AuditActorType.agent,
                {"verdict": "keep", "method": "heuristic_prescreen"},
                job_id=job.id, conversation_id=conv.id, decision_id=decision.id,
            )
            await db.commit()
        except Exception as e:
            await db.rollback()
            print(f"ERROR in _write_heuristic_keep: {e}")


async def _write_standdown_record(conv, run_id, reason, costs):
    async with AsyncSessionLocal() as db:
        try:
            job = AnalysisJob(
                conversation_id=conv.id,
                scheduler_run_id=UUID(run_id),
                status=JobStatus.standdown,
                standdown_reason=reason,
                started_at=now(), completed_at=now(),
                agent_tokens_used=0, agent_cost_usd=0,
            )
            db.add(job)
            await db.flush()

            decision = Decision(
                job_id=job.id, conversation_id=conv.id,
                verdict=Verdict.standdown,
                compression_strategy=None,
                confidence_score=None,
                storage_saving_usd=0, recompute_cost_usd=0,
                agent_cost_usd=0, net_saving_usd=0,
                reasoning=reason, confirmation_required=False,
            )
            db.add(decision)
            await write_audit(
                db, AuditEventType.standdown, AuditActorType.agent,
                {"reason": reason[:200], "level": "batch_gate"},
                job_id=job.id, conversation_id=conv.id,
            )
            await db.commit()
        except Exception as e:
            await db.rollback()
            print(f"ERROR in _write_standdown_record: {e}")


async def _complete_run(run_id, processed, deleted, compressed, saving, agent_cost, net):
    async with AsyncSessionLocal() as db:
        try:
            run = (await db.execute(
                select(SchedulerRun).where(SchedulerRun.id == UUID(run_id))
            )).scalar_one_or_none()
            if run:
                run.status           = SchedulerStatus.completed
                run.jobs_processed   = processed
                run.jobs_deleted     = deleted
                run.jobs_compressed  = compressed
                run.total_saving_usd = saving
                run.agent_cost_usd   = agent_cost
                run.net_saving_usd   = net
                run.completed_at     = now()
                await write_audit(
                    db, AuditEventType.scheduler_run_completed, AuditActorType.agent,
                    {
                        "jobs_processed": processed, "jobs_deleted": deleted,
                        "jobs_compressed": compressed, "net_saving_usd": net,
                    },
                    scheduler_run_id=UUID(run_id),
                )
                await db.commit()
        except Exception as e:
            print(f"ERROR in _complete_run: {e}")


async def _abort_run(run_id, error):
    async with AsyncSessionLocal() as db:
        try:
            run = (await db.execute(
                select(SchedulerRun).where(SchedulerRun.id == UUID(run_id))
            )).scalar_one_or_none()
            if run:
                run.status           = SchedulerStatus.aborted
                run.standdown_reason = error
                run.completed_at     = now()
                await db.commit()
        except Exception as e:
            print(f"ERROR in _abort_run: {e}")
