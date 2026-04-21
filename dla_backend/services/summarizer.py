"""
services/summarizer.py

Generates a 2-3 sentence natural language summary of a conversation
using only metadata already in the database. Zero API cost.

Also provides compute_weighted_access_score() which is used by
the scheduler's heuristic pre-screen and decision rules to replace
raw access_count with a time-decayed engagement score.
"""

import math
from datetime import datetime, timezone
from ..models import Conversation


def now() -> datetime:
    return datetime.now(timezone.utc)


def make_aware(dt: datetime) -> datetime:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Weighted access score ─────────────────────────────────────

DECAY_LAMBDA = 0.01  # controls decay rate
# At λ=0.01:
#   access today        → weight 1.00
#   access 30 days ago  → weight 0.74
#   access 90 days ago  → weight 0.41
#   access 180 days ago → weight 0.17
#   access 365 days ago → weight 0.03


def compute_weighted_access_score(conv: Conversation) -> float:
    """
    Compute a recency-weighted access score using exponential decay.

    Since we don't store individual access timestamps, we approximate
    by distributing accesses evenly between created_at and last_accessed_at.
    Each distributed access gets a decay weight based on how long ago it occurred.

    Returns a float where:
      0.0  = never accessed (or all accesses are very old)
      ~1.0 = accessed once recently
      >1.0 = multiple recent accesses (high engagement signal)

    Why exponential decay: it mirrors how human memory and utility
    work — recent use is a stronger signal of value than historical use.
    """
    if conv.access_count == 0 or conv.last_accessed_at is None:
        return 0.0

    created_at    = make_aware(conv.created_at)
    last_accessed = make_aware(conv.last_accessed_at)
    current       = now()

    # Days since last access (most important signal)
    days_since_last = max((current - last_accessed).days, 0)

    # Days between creation and last access (spread of usage period)
    usage_span_days = max((last_accessed - created_at).days, 0)

    if conv.access_count == 1 or usage_span_days == 0:
        # Single access or all accesses at the same time
        return math.exp(-DECAY_LAMBDA * days_since_last)

    # Distribute accesses evenly across the usage span
    # and compute decay weight for each distributed access point
    total_weight = 0.0
    for i in range(conv.access_count):
        # Fraction along the usage span (0.0 = created_at, 1.0 = last_accessed_at)
        fraction = i / (conv.access_count - 1)
        # Days ago this distributed access occurred
        days_ago = days_since_last + usage_span_days * (1.0 - fraction)
        total_weight += math.exp(-DECAY_LAMBDA * days_ago)

    return total_weight


def weighted_score_label(score: float, access_count: int) -> str:
    """
    Human-readable label for a weighted access score.
    Used in summary sentence 2 and audit log.
    """
    if access_count == 0:
        return "never accessed"
    if score >= 5.0:
        return f"strong recent engagement (weighted score {score:.1f})"
    if score >= 2.0:
        return f"moderate recent engagement (weighted score {score:.1f})"
    if score >= 0.5:
        return f"light engagement, fading over time (weighted score {score:.1f})"
    if score >= 0.1:
        return f"historically accessed, now dormant (weighted score {score:.1f})"
    return f"negligible engagement signal (weighted score {score:.2f})"


# ── Size thresholds ───────────────────────────────────────────
SIZE_TINY   =     5_000
SIZE_SMALL  =    50_000
SIZE_MEDIUM =   500_000
SIZE_LARGE  = 5_000_000

# ── Age thresholds (days) ─────────────────────────────────────
AGE_FRESH   =   7
AGE_RECENT  =  30
AGE_AGED    =  90
AGE_OLD     = 365

# ── Weighted score thresholds ─────────────────────────────────
# These replace raw access_count comparisons throughout the system
WSCORE_HIGH   = 5.0   # strongly active — definitely keep
WSCORE_MEDIUM = 2.0   # moderately active — likely keep
WSCORE_LOW    = 0.5   # light/fading — neutral signal
WSCORE_COLD   = 0.1   # dormant — candidate for action


def generate_summary(conv: Conversation) -> str:
    """
    Generate a 2-3 sentence natural language summary of a conversation
    from its metadata alone. Sentence 2 now uses the weighted access
    score instead of raw access count for a richer engagement picture.
    """
    created_at    = make_aware(conv.created_at)
    last_accessed = make_aware(conv.last_accessed_at)
    age_days      = max((now() - created_at).days, 0)
    wscore        = compute_weighted_access_score(conv)

    sentence_1 = _describe_size_and_age(conv, age_days)
    sentence_2 = _describe_engagement_weighted(conv, last_accessed, wscore)
    sentence_3 = _disposition_signal_weighted(conv, age_days, wscore)

    return f"{sentence_1} {sentence_2} {sentence_3}".strip()


# ── Sentence builders ─────────────────────────────────────────

def _describe_size_and_age(conv: Conversation, age_days: int) -> str:
    size_label = _size_label(conv.size_bytes)
    size_human = _human_size(conv.size_bytes)
    age_label  = _age_label(age_days)

    return (
        f"This is a {size_label} conversation "
        f"({size_human}, {conv.token_count:,} tokens) "
        f"created {age_label}."
    )


def _describe_engagement_weighted(
    conv: Conversation,
    last_accessed: datetime,
    wscore: float,
) -> str:
    """
    Sentence 2: Describe engagement using the weighted access score.
    This replaces the old raw access_count description with a richer
    signal that accounts for how recently the access happened.
    """
    if conv.access_count == 0 or last_accessed is None:
        return "It has never been accessed since creation."

    days_since = (now() - last_accessed).days
    recency    = _recency_label(days_since)
    eng_label  = weighted_score_label(wscore, conv.access_count)

    return (
        f"It was last accessed {recency} "
        f"({conv.access_count} total access(es); {eng_label})."
    )


def _disposition_signal_weighted(
    conv: Conversation,
    age_days: int,
    wscore: float,
) -> str:
    """
    Sentence 3: Disposition signal using weighted score instead of raw count.
    """

    # Strong keep signals
    if wscore >= WSCORE_HIGH:
        return (
            "Its high recency-weighted engagement score indicates strong "
            "active utility — this conversation should be retained."
        )

    if wscore >= WSCORE_MEDIUM:
        return (
            "Its moderate recency-weighted engagement suggests ongoing "
            "value to the user — this conversation is worth keeping."
        )

    # Strong action signals
    if wscore == 0.0 and age_days > AGE_OLD:
        return (
            "A conversation of this age with zero engagement is a "
            "strong candidate for deletion or compression."
        )

    if wscore == 0.0 and age_days > AGE_AGED:
        return (
            "Its age combined with zero engagement suggests it may be "
            "abandoned or archival content — a candidate for compression."
        )

    if wscore == 0.0 and conv.size_bytes > SIZE_LARGE:
        return (
            "Despite its large size, the absence of access history "
            "suggests this may be unused content worth compressing."
        )

    if wscore == 0.0 and age_days > AGE_RECENT:
        return (
            "With no engagement history, this conversation holds no "
            "demonstrated value and may be suitable for compression."
        )

    # Fading engagement
    if wscore < WSCORE_LOW and age_days > AGE_AGED:
        return (
            f"Its low weighted engagement score ({wscore:.2f}) over "
            f"{age_days} days suggests its utility has significantly diminished."
        )

    if wscore < WSCORE_COLD and conv.size_bytes > SIZE_LARGE:
        return (
            f"Its large size relative to its very low weighted engagement "
            f"({wscore:.2f}) makes it a reasonable candidate for compression."
        )

    # Default
    return (
        "Based on available metadata, this conversation shows no strong "
        "signals in either direction — API scoring will determine the verdict."
    )


# ── Label helpers ─────────────────────────────────────────────

def _size_label(size_bytes: int) -> str:
    if size_bytes < SIZE_TINY:   return "tiny"
    if size_bytes < SIZE_SMALL:  return "small"
    if size_bytes < SIZE_MEDIUM: return "medium-sized"
    if size_bytes < SIZE_LARGE:  return "large"
    return "very large"


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1_024:
        return f"{size_bytes}B"
    if size_bytes < 1_024 * 1_024:
        return f"{size_bytes/1_024:.1f}KB"
    return f"{size_bytes/1_024/1_024:.1f}MB"


def _age_label(age_days: int) -> str:
    if age_days == 0:   return "today"
    if age_days == 1:   return "yesterday"
    if age_days < 7:    return f"{age_days} days ago"
    if age_days < 30:
        w = age_days // 7
        return f"{w} week{'s' if w > 1 else ''} ago"
    if age_days < 365:
        m = age_days // 30
        return f"{m} month{'s' if m > 1 else ''} ago"
    y = age_days // 365
    return f"{y} year{'s' if y > 1 else ''} ago"


def _recency_label(days_since: int) -> str:
    if days_since == 0:  return "today"
    if days_since == 1:  return "yesterday"
    if days_since < 7:   return f"{days_since} days ago"
    if days_since < 30:
        w = days_since // 7
        return f"{w} week{'s' if w > 1 else ''} ago"
    if days_since < 365:
        m = days_since // 30
        return f"{m} month{'s' if m > 1 else ''} ago"
    return "over a year ago"
