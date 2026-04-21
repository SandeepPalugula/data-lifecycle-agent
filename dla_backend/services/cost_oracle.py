"""
services/cost_oracle.py
Returns current storage and compute pricing.
Falls back to calibrated defaults if cloud APIs are unreachable.
"""

import psutil
from datetime import datetime

# S3 Standard: $0.023 per GB per MONTH = $0.023/30 per GB per DAY
DEFAULT_STORAGE_COST_PER_GB_DAY = 0.023 / 30   # = $0.000767 / GB / day

# Claude Sonnet input pricing per 1K tokens
DEFAULT_COMPUTE_COST_PER_KTOK = 0.003


def get_current_costs() -> dict:
    """
    Returns current cost rates.
    In production, polls AWS Cost Explorer API.
    For now returns calibrated defaults with peak pricing applied.
    """
    cpu_load   = psutil.cpu_percent(interval=0.5)
    hour       = datetime.utcnow().hour
    is_peak    = (8 <= hour <= 10) or (17 <= hour <= 20)
    peak_factor = 2.0 if is_peak else 1.0

    return {
        "storage_cost_per_gb_day": DEFAULT_STORAGE_COST_PER_GB_DAY,
        "compute_cost_per_ktok":   DEFAULT_COMPUTE_COST_PER_KTOK * peak_factor,
        "peak_factor":             peak_factor,
        "compute_load_pct":        int(cpu_load),
        "provider":                "default",
    }


def compute_net_saving(
    size_bytes:       int,
    uniqueness_score: float,
    token_count:      int,
    agent_tokens:     int,
    costs:            dict,
    retention_days:   int = 30,
) -> dict:
    """
    Core cost comparison formula.

    storage_saving  = how much we save by deleting the conversation
    recompute_cost  = penalty if we ever need this content regenerated
    agent_cost      = what the analysis itself cost to run
    net_saving      = storage_saving - recompute_cost - agent_cost
    """
    size_gb = size_bytes / 1024 / 1024 / 1024  # bytes → GB

    storage_saving = (
        size_gb
        * float(costs["storage_cost_per_gb_day"])
        * retention_days
    )
    recompute_cost = (
        uniqueness_score
        * (token_count / 1000)
        * float(costs["compute_cost_per_ktok"])
        * 0.4
    )
    agent_cost = (
        (agent_tokens / 1000)
        * float(costs["compute_cost_per_ktok"])
        * 1.12  # 12% overhead for confirm + audit steps
    )
    net_saving = storage_saving - recompute_cost - agent_cost

    return {
        "storage_saving_usd": round(storage_saving, 8),
        "recompute_cost_usd": round(recompute_cost, 8),
        "agent_cost_usd":     round(agent_cost, 8),
        "net_saving_usd":     round(net_saving, 8),
    }
