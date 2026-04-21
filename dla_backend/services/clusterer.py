"""
services/clusterer.py

Groups candidate conversations into clusters based on metadata similarity.
Pure Python — no API call, no ML library needed.

Clustering dimensions:
  1. Size bucket    — how large is the conversation?
  2. Age bucket     — how old is it?
  3. Engagement bucket — how much has it been accessed?

A conversation's cluster key is the combination of all three buckets.
Conversations sharing a key are grouped together.

Within each cluster, the representative is the conversation whose
combined metrics sit closest to the cluster median — the most
"typical" member for Claude to reason about.

Why this approach:
  - Zero cost (no embeddings, no API call)
  - Deterministic and explainable
  - Dimensions are directly meaningful to storage economics
  - Scales linearly with conversation count
"""

from datetime import datetime, timezone
from dataclasses import dataclass
from ..models import Conversation


def now() -> datetime:
    return datetime.now(timezone.utc)


def make_aware(dt: datetime) -> datetime:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Bucket definitions ────────────────────────────────────────

def size_bucket(size_bytes: int) -> str:
    if size_bytes < 50_000:        return "tiny"
    if size_bytes < 500_000:       return "small"
    if size_bytes < 5_000_000:     return "medium"
    return "large"


def age_bucket(age_days: int) -> str:
    if age_days < 7:    return "fresh"
    if age_days < 30:   return "recent"
    if age_days < 180:  return "aged"
    return "old"


def engagement_bucket(access_count: int) -> str:
    if access_count == 0:   return "cold"
    if access_count <= 5:   return "warm"
    return "hot"


def cluster_key(conv: Conversation) -> str:
    """
    Returns the cluster key for a conversation.
    Format: '{size}_{age}_{engagement}'
    Example: 'large_fresh_cold', 'small_aged_warm'
    """
    created_at = make_aware(conv.created_at)
    age_days   = max((now() - created_at).days, 0)

    return (
        f"{size_bucket(conv.size_bytes)}_"
        f"{age_bucket(age_days)}_"
        f"{engagement_bucket(conv.access_count)}"
    )


# ── Cluster data structure ────────────────────────────────────

@dataclass
class ConversationCluster:
    key:            str
    members:        list[Conversation]
    representative: Conversation
    summary:        str  # Human-readable description of what this cluster represents

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def non_representatives(self) -> list[Conversation]:
        return [m for m in self.members if m.id != self.representative.id]


# ── Main clustering function ──────────────────────────────────

def cluster_conversations(
    conversations: list[Conversation],
    summaries: dict,  # conv.id → generated summary string
) -> list[ConversationCluster]:
    """
    Groups conversations into clusters and selects a representative
    for each cluster.

    Args:
        conversations: list of candidate Conversation objects
        summaries: dict mapping conversation.id to pre-generated summary string

    Returns:
        list of ConversationCluster objects, one per unique cluster key
    """
    if not conversations:
        return []

    # Group conversations by cluster key
    groups: dict[str, list[Conversation]] = {}
    for conv in conversations:
        key = cluster_key(conv)
        if key not in groups:
            groups[key] = []
        groups[key].append(conv)

    clusters = []
    for key, members in groups.items():
        representative = _select_representative(members)
        cluster_summary = _describe_cluster(key, members)
        clusters.append(ConversationCluster(
            key=key,
            members=members,
            representative=representative,
            summary=cluster_summary,
        ))

    # Sort clusters by size descending — largest clusters first
    # so the biggest savings opportunities are processed first
    clusters.sort(key=lambda c: c.size, reverse=True)

    return clusters


def _select_representative(members: list[Conversation]) -> Conversation:
    """
    Select the conversation closest to the cluster median.
    Uses a simple scoring approach: rank each conversation on
    size, age, and access count, then pick the one with the
    median rank sum.

    For single-member clusters, returns that member directly.
    """
    if len(members) == 1:
        return members[0]

    # Calculate median values for the cluster
    sizes   = sorted(m.size_bytes for m in members)
    ages    = sorted(
        max((now() - make_aware(m.created_at)).days, 0)
        for m in members
    )
    accesses = sorted(m.access_count for m in members)

    median_size   = sizes[len(sizes) // 2]
    median_age    = ages[len(ages) // 2]
    median_access = accesses[len(accesses) // 2]

    # Score each member by distance from median
    # Lower score = closer to median = better representative
    def distance(conv: Conversation) -> float:
        age_days = max((now() - make_aware(conv.created_at)).days, 0)
        # Normalise each dimension to 0-1 scale before combining
        size_range   = max(sizes[-1] - sizes[0], 1)
        age_range    = max(ages[-1] - ages[0], 1)
        access_range = max(accesses[-1] - accesses[0], 1)

        size_dist   = abs(conv.size_bytes - median_size) / size_range
        age_dist    = abs(age_days - median_age) / age_range
        access_dist = abs(conv.access_count - median_access) / access_range

        return size_dist + age_dist + access_dist

    return min(members, key=distance)


def _describe_cluster(key: str, members: list[Conversation]) -> str:
    """
    Generate a human-readable description of what this cluster represents.
    Used in audit log and decision reasoning for non-representative members.
    """
    parts      = key.split("_")
    size_label = parts[0]
    age_label  = parts[1]
    eng_label  = parts[2]

    count = len(members)
    conv_word = "conversation" if count == 1 else "conversations"

    size_desc = {
        "tiny":   "very small (<50KB)",
        "small":  "small (50KB–500KB)",
        "medium": "medium-sized (500KB–5MB)",
        "large":  "large (>5MB)",
    }.get(size_label, size_label)

    age_desc = {
        "fresh":  "recently created (<7 days)",
        "recent": "relatively new (7–30 days)",
        "aged":   "moderately old (30–180 days)",
        "old":    "old (>180 days)",
    }.get(age_label, age_label)

    eng_desc = {
        "cold": "never accessed",
        "warm": "occasionally accessed (1–5 times)",
        "hot":  "frequently accessed (>5 times)",
    }.get(eng_label, eng_label)

    return (
        f"Cluster '{key}': {count} {conv_word} that are "
        f"{size_desc}, {age_desc}, and {eng_desc}."
    )


def format_cluster_verdict_reasoning(
    cluster: ConversationCluster,
    representative_reasoning: str,
    representative_id: str,
) -> str:
    """
    Build the reasoning text for a non-representative cluster member.
    Explains why this conversation received the verdict it did,
    referencing the representative's scoring.
    """
    return (
        f"Verdict assigned by cluster analysis.\n\n"
        f"Cluster: {cluster.summary}\n\n"
        f"This conversation was grouped with {cluster.size - 1} similar "
        f"conversation(s) based on size, age, and engagement pattern. "
        f"One representative conversation was scored by the Anthropic API "
        f"and this verdict was applied to all cluster members.\n\n"
        f"Representative reasoning:\n{representative_reasoning}\n\n"
        f"Representative ID: {representative_id}\n\n"
        f"Note: Confirmation is still required for this action — "
        f"cluster assignment does not bypass human review."
    )
