"""
services/decision_engine.py
Full analysis pipeline for one conversation job.
"""

import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..models import (
    Conversation, ConversationState, SafetyFlag,
    AnalysisJob, JobStatus, Decision, Verdict,
    CostSnapshot, AuditEventType, AuditActorType
)
from ..services.scorer import score_conversation
from ..services.cost_oracle import get_current_costs
from ..audit import write_audit
from ..config import settings

# Use this everywhere — timezone-aware UTC, compatible with PostgreSQL timestamps
def now() -> datetime:
    return datetime.now(timezone.utc)


async def run_analysis_job(job_id: uuid.UUID, db: AsyncSession) -> dict:
    """
    Run the full analysis pipeline for one conversation job.
    Returns a summary dict with the verdict and key metrics.
    """

    # ── Step 1: Load the job ──────────────────────────────────
    job_result = await db.execute(
        select(AnalysisJob).where(AnalysisJob.id == job_id)
    )
    job = job_result.scalar_one_or_none()
    if not job:
        return {"error": f"Job {job_id} not found"}

    job.status     = JobStatus.running
    job.started_at = now()
    await db.flush()

    await write_audit(
        db, AuditEventType.job_started, AuditActorType.agent,
        {"job_id": str(job_id)},
        job_id=job_id, conversation_id=job.conversation_id
    )

    try:
        # ── Step 2: Load the conversation ─────────────────────
        conv_result = await db.execute(
            select(Conversation).where(Conversation.id == job.conversation_id)
        )
        conv = conv_result.scalar_one_or_none()
        if not conv:
            return await _fail_job(job, db, "Conversation not found")

        # ── Step 3: Safety flag check ─────────────────────────
        flag_result = await db.execute(
            select(SafetyFlag).where(SafetyFlag.conversation_id == conv.id)
        )
        flag = flag_result.scalar_one_or_none()
        if flag:
            await write_audit(
                db, AuditEventType.safety_block, AuditActorType.agent,
                {"reason": flag.flag_reason},
                job_id=job_id, conversation_id=conv.id
            )
            return await _write_verdict(
                job, conv, db,
                verdict=Verdict.keep,
                reasoning=f"Safety flagged: {flag.flag_reason}.",
                storage_saving=0, recompute_cost=0,
                agent_cost=0, net_saving=0,
                uniqueness=None, utility=None,
                confirmation_required=False,
            )

        # ── Step 4: Fetch current costs ───────────────────────
        costs = get_current_costs()

        snapshot = CostSnapshot(
            provider=costs["provider"],
            storage_cost_per_gb_day=costs["storage_cost_per_gb_day"],
            compute_cost_per_ktok=costs["compute_cost_per_ktok"],
            peak_factor=costs["peak_factor"],
        )
        db.add(snapshot)
        await db.flush()
        job.cost_snapshot_id = snapshot.id

        # ── Step 5: Estimate agent's own cost ─────────────────
        estimated_agent_tokens = settings.AGENT_TOKENS_PER_CALL
        agent_cost_estimate = (
            estimated_agent_tokens / 1000
        ) * float(costs["compute_cost_per_ktok"]) * 1.12

        # ── Step 6: Stand-down check ──────────────────────────
        # SKIP_STANDDOWN_CHECK=true in .env bypasses this block entirely.
        if not settings.SKIP_STANDDOWN_CHECK:
            compute_load = costs.get("compute_load_pct", 0)
            peak_factor  = float(costs["peak_factor"])

            if compute_load > settings.AGENT_MAX_COMPUTE_LOAD_PCT:
                reason = f"Compute load {compute_load}% exceeds threshold {settings.AGENT_MAX_COMPUTE_LOAD_PCT}%"
                return await _standdown(job, conv, db, reason, agent_cost_estimate, estimated_agent_tokens)

            if peak_factor > settings.AGENT_PEAK_FACTOR_LIMIT:
                reason = f"Peak pricing {peak_factor}x exceeds limit {settings.AGENT_PEAK_FACTOR_LIMIT}x"
                return await _standdown(job, conv, db, reason, agent_cost_estimate, estimated_agent_tokens)

            size_gb      = conv.size_bytes / 1024 / 1024 / 1024
            rough_saving = size_gb * float(costs["storage_cost_per_gb_day"]) * 30
            threshold    = rough_saving * settings.AGENT_STANDDOWN_THRESHOLD

            if rough_saving > 0 and agent_cost_estimate > threshold:
                reason = (
                    f"Agent cost ${agent_cost_estimate:.6f} exceeds "
                    f"{settings.AGENT_STANDDOWN_THRESHOLD * 100:.0f}% "
                    f"of projected saving ${rough_saving:.6f}"
                )
                return await _standdown(job, conv, db, reason, agent_cost_estimate, estimated_agent_tokens)

        # ── Step 7: Semantic scoring via Anthropic API ────────
        # Make created_at timezone-aware for safe comparison
        created_at = conv.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = (now() - created_at).days

        conv_description = (
            f"Conversation metadata:\n"
            f"- Age: {age_days} days old\n"
            f"- Size: {conv.size_bytes} bytes ({conv.size_bytes/1024:.1f} KB)\n"
            f"- Token count: {conv.token_count}\n"
            f"- Access count: {conv.access_count}\n"
            f"- Last accessed: {conv.last_accessed_at or 'never'}\n"
            f"- State: {conv.state.value}\n"
            f"- External ID: {conv.external_id}"
        )

        try:
            scores     = await score_conversation(conv_description)
            uniqueness = float(scores.get("uniqueness_score", 0.5))
            utility    = float(scores.get("utility_value", 0.5))
            reasoning  = scores.get("reasoning", "No reasoning provided.")
            actual_tokens = (
                scores.get("input_tokens", 0) + scores.get("output_tokens", 0)
            ) or estimated_agent_tokens
        except Exception as e:
            uniqueness    = _heuristic_uniqueness(conv)
            utility       = _heuristic_utility(conv)
            reasoning     = f"API scoring unavailable ({e}). Heuristic scores applied."
            actual_tokens = estimated_agent_tokens

        actual_agent_cost = (
            actual_tokens / 1000
        ) * float(costs["compute_cost_per_ktok"]) * 1.12

        job.agent_tokens_used = actual_tokens
        job.agent_cost_usd    = actual_agent_cost
        conv.uniqueness_score = uniqueness
        conv.utility_score    = utility
        conv.last_scored_at   = now()

        # ── Step 8: Cost comparison ───────────────────────────
        size_gb        = conv.size_bytes / 1024 / 1024 / 1024
        storage_saving = size_gb * float(costs["storage_cost_per_gb_day"]) * 30
        recompute_cost = (
            uniqueness
            * (conv.token_count / 1000)
            * float(costs["compute_cost_per_ktok"])
            * 0.4
        )
        net_saving = storage_saving - recompute_cost - actual_agent_cost

        # ── Step 9: Decision rules ────────────────────────────
        verdict, needs_confirmation = _apply_rules(
            age_days=age_days,
            access_count=conv.access_count,
            uniqueness=uniqueness,
            utility=utility,
            net_saving=net_saving,
        )

        # ── Step 10: Write verdict ────────────────────────────
        return await _write_verdict(
            job, conv, db,
            verdict=verdict,
            reasoning=reasoning,
            storage_saving=storage_saving,
            recompute_cost=recompute_cost,
            agent_cost=actual_agent_cost,
            net_saving=net_saving,
            uniqueness=uniqueness,
            utility=utility,
            confirmation_required=needs_confirmation,
        )

    except Exception as e:
        return await _fail_job(job, db, str(e))


# ── Decision rules ────────────────────────────────────────────

def _apply_rules(age_days, access_count, uniqueness, utility, net_saving):
    """Apply decision rules. Returns (verdict, confirmation_required)."""

    # Never delete if net saving is negative
    if net_saving < 0:
        return Verdict.keep, False

    # Keep if recently and frequently accessed
    if access_count > 3 and age_days < 30:
        return Verdict.keep, False

    # Delete if very low value, never accessed, older than 30 days
    if uniqueness < 0.2 and utility < 0.2 and access_count == 0 and age_days > 30:
        return Verdict.delete, True

    # Compress if moderate value or old with positive net saving
    if (uniqueness < 0.5 or age_days > 180) and net_saving > 0:
        return Verdict.compress, True

    return Verdict.keep, False


# ── Heuristic scoring fallback ────────────────────────────────

def _heuristic_uniqueness(conv) -> float:
    score = 0.5
    if conv.token_count > 2000: score += 0.2
    if conv.access_count > 5:   score += 0.2
    if conv.token_count < 200:  score -= 0.3
    return max(0.0, min(1.0, score))

def _heuristic_utility(conv) -> float:
    created_at = conv.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_days = (now() - created_at).days
    score = 0.5
    if conv.access_count > 3: score += 0.3
    if age_days > 365:        score -= 0.3
    if age_days < 7:          score += 0.2
    return max(0.0, min(1.0, score))


# ── Write verdict to DB ───────────────────────────────────────

async def _write_verdict(
    job, conv, db, verdict, reasoning,
    storage_saving, recompute_cost, agent_cost, net_saving,
    uniqueness, utility, confirmation_required,
) -> dict:

    conf_token   = str(uuid.uuid4()) if confirmation_required else None
    conf_expires = now() + timedelta(hours=24) if confirmation_required else None

    decision = Decision(
        job_id=job.id,
        conversation_id=conv.id,
        verdict=verdict,
        storage_saving_usd=storage_saving,
        recompute_cost_usd=recompute_cost,
        agent_cost_usd=agent_cost,
        net_saving_usd=net_saving,
        uniqueness_score=uniqueness,
        utility_score=utility,
        reasoning=reasoning,
        confirmation_required=confirmation_required,
        confirmation_token=conf_token,
        confirmation_expires_at=conf_expires,
    )
    db.add(decision)

    if verdict == Verdict.delete and not confirmation_required:
        conv.state = ConversationState.deleted
    elif verdict == Verdict.compress and not confirmation_required:
        conv.state = ConversationState.compressed
    else:
        conv.state = ConversationState.active

    job.status       = JobStatus.completed
    job.completed_at = now()
    await db.flush()

    await write_audit(
        db, AuditEventType.verdict_issued, AuditActorType.agent,
        {
            "verdict": verdict.value,
            "net_saving_usd": net_saving,
            "confirmation_required": confirmation_required,
            "reasoning": reasoning[:200] if reasoning else None,
        },
        job_id=job.id,
        conversation_id=conv.id,
        decision_id=decision.id,
    )

    return {
        "verdict": verdict.value,
        "net_saving_usd": net_saving,
        "agent_cost_usd": agent_cost,
        "confirmation_required": confirmation_required,
        "reasoning": reasoning,
    }


async def _standdown(job, conv, db, reason, agent_cost, tokens) -> dict:
    job.status            = JobStatus.standdown
    job.standdown_reason  = reason
    job.agent_cost_usd    = agent_cost
    job.agent_tokens_used = tokens
    job.completed_at      = now()

    decision = Decision(
        job_id=job.id,
        conversation_id=conv.id,
        verdict=Verdict.standdown,
        storage_saving_usd=0,
        recompute_cost_usd=0,
        agent_cost_usd=agent_cost,
        net_saving_usd=0,
        reasoning=reason,
        confirmation_required=False,
    )
    db.add(decision)
    await db.flush()

    await write_audit(
        db, AuditEventType.standdown, AuditActorType.agent,
        {"reason": reason, "agent_cost": agent_cost},
        job_id=job.id, conversation_id=conv.id,
    )

    return {"verdict": "standdown", "reason": reason, "agent_cost_usd": agent_cost}


async def _fail_job(job, db, error: str) -> dict:
    job.status        = JobStatus.failed
    job.error_message = error
    job.completed_at  = now()
    await db.flush()

    await write_audit(
        db, AuditEventType.job_failed, AuditActorType.agent,
        {"error": error}, job_id=job.id,
    )

    return {"error": error}
