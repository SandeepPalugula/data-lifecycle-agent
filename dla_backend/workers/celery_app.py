"""
workers/celery_app.py

Celery application configuration.
Why Celery: it gives us distributed task queuing — the scheduler
can dispatch hundreds of jobs and multiple worker processes pick
them up in parallel. Redis acts as the message broker (the queue)
and the result backend (stores job outcomes).
"""

from celery import Celery
from ..config import settings

celery_app = Celery(
    "dla_agent",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["dla_backend.workers.tasks"],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Task behaviour
    task_acks_late=True,          # Only ack after task completes (safer)
    task_reject_on_worker_lost=True,  # Re-queue if worker crashes mid-task
    worker_prefetch_multiplier=1, # One task at a time per worker (fairer)

    # Result expiry — we don't need Celery results since we write to DB
    result_expires=3600,

    # Beat schedule — the autonomous cron that runs the batch
    beat_schedule={
        "run-batch-offpeak": {
            "task": "dla_backend.workers.tasks.run_scheduled_batch",
            "schedule": 3600.0,   # Every hour — decision engine checks if it's off-peak
            "options": {"queue": "scheduler"},
        },
    },
)
