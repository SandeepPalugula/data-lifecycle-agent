"""
models.py
SQLAlchemy ORM models — mirror of dla_schema.sql.
All enums use native_enum=False to avoid PostgreSQL type casting issues.
"""

import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import (
    String, Integer, Float, Boolean, Text, Numeric,
    ForeignKey, Enum as SAEnum, BigInteger, TIMESTAMP
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, INET
from sqlalchemy.orm import relationship, Mapped, mapped_column
from .database import Base

# ── Enums ─────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    viewer  = "viewer"
    analyst = "analyst"
    admin   = "admin"

class ConversationState(str, enum.Enum):
    active            = "active"
    pending_analysis  = "pending_analysis"
    compressed        = "compressed"
    deleted           = "deleted"
    safety_locked     = "safety_locked"

class JobStatus(str, enum.Enum):
    queued    = "queued"
    running   = "running"
    completed = "completed"
    failed    = "failed"
    standdown = "standdown"

class Verdict(str, enum.Enum):
    keep      = "keep"
    compress  = "compress"
    delete    = "delete"
    standdown = "standdown"

class CompressionStrategy(str, enum.Enum):
    summary   = "summary"
    keypoints = "keypoints"
    qa        = "qa"

class SchedulerTrigger(str, enum.Enum):
    cron   = "cron"
    manual = "manual"
    api    = "api"

class SchedulerStatus(str, enum.Enum):
    running   = "running"
    completed = "completed"
    aborted   = "aborted"
    standdown = "standdown"

class AuditEventType(str, enum.Enum):
    job_queued               = "job_queued"
    job_started              = "job_started"
    job_completed            = "job_completed"
    job_failed               = "job_failed"
    verdict_issued           = "verdict_issued"
    confirmation_sent        = "confirmation_sent"
    confirmation_received    = "confirmation_received"
    deletion_executed        = "deletion_executed"
    compression_executed     = "compression_executed"
    standdown                = "standdown"
    safety_block             = "safety_block"
    auth_login               = "auth_login"
    auth_logout              = "auth_logout"
    settings_changed         = "settings_changed"
    scheduler_run_started    = "scheduler_run_started"
    scheduler_run_completed  = "scheduler_run_completed"

class AuditActorType(str, enum.Enum):
    agent  = "agent"
    user   = "user"
    system = "system"

class SafetyReviewStatus(str, enum.Enum):
    pending  = "pending"
    reviewed = "reviewed"
    cleared  = "cleared"

# ── Helper: enum column that works with existing PostgreSQL enum types ──

def col_enum(enum_class):
    """Returns a SAEnum configured to avoid native type casting errors."""
    return SAEnum(
        enum_class,
        native_enum=False,
        create_constraint=False,
        length=64,
    )

# ── UTC now helper ────────────────────────────────────────────
def utcnow() -> datetime:
    return datetime.now(timezone.utc)

# ── Models ────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id         : Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email      : Mapped[str]        = mapped_column(String(320), unique=True, nullable=False)
    role       : Mapped[UserRole]   = mapped_column(col_enum(UserRole), nullable=False, default=UserRole.analyst)
    settings   : Mapped[dict]       = mapped_column(JSONB, nullable=False, default=dict)
    created_at : Mapped[datetime]   = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
    updated_at : Mapped[datetime]   = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)

    conversations  = relationship("Conversation",  back_populates="user")
    scheduler_runs = relationship("SchedulerRun",  back_populates="triggered_by_user_rel")


class Conversation(Base):
    __tablename__ = "conversations"

    id                : Mapped[uuid.UUID]         = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id           : Mapped[uuid.UUID]         = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    external_id       : Mapped[str]               = mapped_column(String(512), unique=True, nullable=False)
    size_bytes        : Mapped[int]               = mapped_column(Integer, nullable=False, default=0)
    token_count       : Mapped[int]               = mapped_column(Integer, nullable=False, default=0)
    access_count      : Mapped[int]               = mapped_column(Integer, nullable=False, default=0)
    last_accessed_at  : Mapped[datetime | None]   = mapped_column(TIMESTAMP(timezone=True))
    state             : Mapped[ConversationState] = mapped_column(col_enum(ConversationState), nullable=False, default=ConversationState.active)
    uniqueness_score  : Mapped[float | None]      = mapped_column(Float)
    utility_score     : Mapped[float | None]      = mapped_column(Float)
    last_scored_at    : Mapped[datetime | None]   = mapped_column(TIMESTAMP(timezone=True))
    compression_ratio : Mapped[float | None]      = mapped_column(Float)
    compressed_at     : Mapped[datetime | None]   = mapped_column(TIMESTAMP(timezone=True))
    created_at        : Mapped[datetime]          = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
    updated_at        : Mapped[datetime]          = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)

    user          = relationship("User",        back_populates="conversations")
    analysis_jobs = relationship("AnalysisJob", back_populates="conversation")
    decisions     = relationship("Decision",    back_populates="conversation")
    safety_flag   = relationship("SafetyFlag",  back_populates="conversation", uselist=False)


class SafetyFlag(Base):
    __tablename__ = "safety_flags"

    conversation_id   : Mapped[uuid.UUID]          = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True)
    flag_reason       : Mapped[str]                = mapped_column(String(1024), nullable=False)
    flagged_by_system : Mapped[str]                = mapped_column(String(128), nullable=False)
    review_status     : Mapped[SafetyReviewStatus] = mapped_column(col_enum(SafetyReviewStatus), nullable=False, default=SafetyReviewStatus.pending)
    flagged_at        : Mapped[datetime]           = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
    reviewed_at       : Mapped[datetime | None]    = mapped_column(TIMESTAMP(timezone=True))
    reviewed_by       : Mapped[uuid.UUID | None]   = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    conversation = relationship("Conversation", back_populates="safety_flag")


class CostSnapshot(Base):
    __tablename__ = "cost_snapshots"

    id                      : Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider                : Mapped[str]       = mapped_column(String(64), nullable=False)
    region                  : Mapped[str | None]= mapped_column(String(64))
    storage_cost_per_gb_day : Mapped[float]     = mapped_column(Numeric(12, 8), nullable=False)
    compute_cost_per_ktok   : Mapped[float]     = mapped_column(Numeric(12, 8), nullable=False)
    peak_factor             : Mapped[float]     = mapped_column(Numeric(6, 3), nullable=False, default=1.0)
    captured_at             : Mapped[datetime]  = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)


class SchedulerRun(Base):
    __tablename__ = "scheduler_runs"

    id                : Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    triggered_by      : Mapped[SchedulerTrigger] = mapped_column(col_enum(SchedulerTrigger), nullable=False, default=SchedulerTrigger.cron)
    triggered_by_user : Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    status            : Mapped[SchedulerStatus]  = mapped_column(col_enum(SchedulerStatus), nullable=False, default=SchedulerStatus.running)
    standdown_reason  : Mapped[str | None]       = mapped_column(String(512))
    jobs_queued       : Mapped[int]              = mapped_column(Integer, nullable=False, default=0)
    jobs_processed    : Mapped[int]              = mapped_column(Integer, nullable=False, default=0)
    jobs_kept         : Mapped[int]              = mapped_column(Integer, nullable=False, default=0)
    jobs_compressed   : Mapped[int]              = mapped_column(Integer, nullable=False, default=0)
    jobs_deleted      : Mapped[int]              = mapped_column(Integer, nullable=False, default=0)
    jobs_failed       : Mapped[int]              = mapped_column(Integer, nullable=False, default=0)
    total_saving_usd  : Mapped[float]            = mapped_column(Numeric(12, 6), nullable=False, default=0)
    agent_cost_usd    : Mapped[float]            = mapped_column(Numeric(12, 6), nullable=False, default=0)
    net_saving_usd    : Mapped[float]            = mapped_column(Numeric(12, 6), nullable=False, default=0)
    compute_load_pct  : Mapped[int | None]       = mapped_column(Integer)
    peak_factor       : Mapped[float | None]     = mapped_column(Numeric(6, 3))
    started_at        : Mapped[datetime]         = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
    completed_at      : Mapped[datetime | None]  = mapped_column(TIMESTAMP(timezone=True))

    triggered_by_user_rel = relationship("User",        back_populates="scheduler_runs")
    analysis_jobs         = relationship("AnalysisJob", back_populates="scheduler_run")


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id               : Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id  : Mapped[uuid.UUID]        = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    scheduler_run_id : Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scheduler_runs.id"))
    status           : Mapped[JobStatus]        = mapped_column(col_enum(JobStatus), nullable=False, default=JobStatus.queued)
    standdown_reason : Mapped[str | None]       = mapped_column(String(512))
    agent_tokens_used: Mapped[int]              = mapped_column(Integer, nullable=False, default=0)
    agent_cost_usd   : Mapped[float]            = mapped_column(Numeric(12, 8), nullable=False, default=0)
    cost_snapshot_id : Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cost_snapshots.id"))
    created_at       : Mapped[datetime]         = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)
    started_at       : Mapped[datetime | None]  = mapped_column(TIMESTAMP(timezone=True))
    completed_at     : Mapped[datetime | None]  = mapped_column(TIMESTAMP(timezone=True))
    error_message    : Mapped[str | None]       = mapped_column(Text)

    conversation  = relationship("Conversation",  back_populates="analysis_jobs")
    scheduler_run = relationship("SchedulerRun",  back_populates="analysis_jobs")
    decision      = relationship("Decision",      back_populates="job", uselist=False)


class Decision(Base):
    __tablename__ = "decisions"

    id                      : Mapped[uuid.UUID]                  = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id                  : Mapped[uuid.UUID]                  = mapped_column(UUID(as_uuid=True), ForeignKey("analysis_jobs.id", ondelete="CASCADE"), unique=True, nullable=False)
    conversation_id         : Mapped[uuid.UUID]                  = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    verdict                 : Mapped[Verdict]                    = mapped_column(col_enum(Verdict), nullable=False)
    compression_strategy    : Mapped[CompressionStrategy | None] = mapped_column(col_enum(CompressionStrategy))
    confidence_score        : Mapped[float | None]               = mapped_column(Float)
    storage_saving_usd      : Mapped[float]                      = mapped_column(Numeric(12, 8), nullable=False, default=0)
    recompute_cost_usd      : Mapped[float]                      = mapped_column(Numeric(12, 8), nullable=False, default=0)
    agent_cost_usd          : Mapped[float]                      = mapped_column(Numeric(12, 8), nullable=False, default=0)
    net_saving_usd          : Mapped[float]                      = mapped_column(Numeric(12, 8), nullable=False, default=0)
    uniqueness_score        : Mapped[float | None]               = mapped_column(Float)
    utility_score           : Mapped[float | None]               = mapped_column(Float)
    reasoning               : Mapped[str | None]                 = mapped_column(Text)
    confirmation_required   : Mapped[bool]                       = mapped_column(Boolean, nullable=False, default=False)
    confirmation_token      : Mapped[str | None]                 = mapped_column(String(512), unique=True)
    confirmation_expires_at : Mapped[datetime | None]            = mapped_column(TIMESTAMP(timezone=True))
    confirmed_at            : Mapped[datetime | None]            = mapped_column(TIMESTAMP(timezone=True))
    confirmed_by            : Mapped[uuid.UUID | None]           = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    rejected_at             : Mapped[datetime | None]            = mapped_column(TIMESTAMP(timezone=True))
    rejected_by             : Mapped[uuid.UUID | None]           = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    executed_at             : Mapped[datetime | None]            = mapped_column(TIMESTAMP(timezone=True))
    created_at              : Mapped[datetime]                   = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)

    job          = relationship("AnalysisJob",  back_populates="decision")
    conversation = relationship("Conversation", back_populates="decisions")
    compression  = relationship("Compression",  back_populates="decision", uselist=False)
    deletion     = relationship("Deletion",     back_populates="decision", uselist=False)


class Compression(Base):
    __tablename__ = "compressions"

    id                       : Mapped[uuid.UUID]           = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_id              : Mapped[uuid.UUID]           = mapped_column(UUID(as_uuid=True), ForeignKey("decisions.id", ondelete="CASCADE"), unique=True, nullable=False)
    conversation_id          : Mapped[uuid.UUID]           = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    strategy                 : Mapped[CompressionStrategy] = mapped_column(col_enum(CompressionStrategy), nullable=False)
    original_size_bytes      : Mapped[int]                 = mapped_column(Integer, nullable=False)
    compressed_size_bytes    : Mapped[int]                 = mapped_column(Integer, nullable=False)
    compression_ratio        : Mapped[float]               = mapped_column(Float, nullable=False)
    tokens_used              : Mapped[int]                 = mapped_column(Integer, nullable=False, default=0)
    cost_usd                 : Mapped[float]               = mapped_column(Numeric(12, 8), nullable=False, default=0)
    original_preserved_until : Mapped[datetime | None]     = mapped_column(TIMESTAMP(timezone=True))
    rolled_back_at           : Mapped[datetime | None]     = mapped_column(TIMESTAMP(timezone=True))
    created_at               : Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)

    decision = relationship("Decision", back_populates="compression")


class Deletion(Base):
    __tablename__ = "deletions"

    id                  : Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_id         : Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("decisions.id", ondelete="CASCADE"), unique=True, nullable=False)
    conversation_id     : Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    confirmed_by        : Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    storage_freed_bytes : Mapped[int]       = mapped_column(Integer, nullable=False, default=0)
    net_saving_usd      : Mapped[float]     = mapped_column(Numeric(12, 8), nullable=False, default=0)
    confirmed_at        : Mapped[datetime]  = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    executed_at         : Mapped[datetime]  = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)

    decision = relationship("Decision", back_populates="deletion")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id               : Mapped[int]               = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type       : Mapped[AuditEventType]    = mapped_column(col_enum(AuditEventType), nullable=False)
    actor_type       : Mapped[AuditActorType]    = mapped_column(col_enum(AuditActorType), nullable=False)
    actor_id         : Mapped[uuid.UUID | None]  = mapped_column(UUID(as_uuid=True))
    conversation_id  : Mapped[uuid.UUID | None]  = mapped_column(UUID(as_uuid=True))
    job_id           : Mapped[uuid.UUID | None]  = mapped_column(UUID(as_uuid=True))
    decision_id      : Mapped[uuid.UUID | None]  = mapped_column(UUID(as_uuid=True))
    scheduler_run_id : Mapped[uuid.UUID | None]  = mapped_column(UUID(as_uuid=True))
    detail           : Mapped[dict]              = mapped_column(JSONB, nullable=False, default=dict)
    ip_address       : Mapped[str | None]        = mapped_column(INET)
    user_agent       : Mapped[str | None]        = mapped_column(String(512))
    created_at       : Mapped[datetime]          = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=utcnow)