"""
services/forecaster.py

Calculates projected savings forecasts for individual decisions
and batch runs. Computed on the fly — no database schema changes needed.

Key concepts:
  - Storage saving is recurring (every month you keep the data deleted
    or compressed, you save that month's storage cost)
  - Agent cost is one-time (paid once when the analysis ran)
  - Break-even = how many months until recurring savings cover agent cost
  - Compression ratio = how much smaller a compressed conversation is
    (we assume 70% reduction, i.e. compressed size = 30% of original)
"""

from dataclasses import dataclass

# Compression ratio: compressed conversation is 30% of original size
COMPRESSION_RATIO = 0.30

# Forecast horizons in months
FORECAST_HORIZONS = [3, 6, 12]


@dataclass
class DecisionForecast:
    """
    Projected savings for a single decision at multiple time horizons.
    All monetary values in USD.
    """
    verdict:              str
    monthly_saving_usd:   float   # recurring saving per month
    agent_cost_usd:       float   # one-time cost (already paid)
    break_even_months:    float   # months until savings cover agent cost
    forecast_3m_usd:      float   # projected saving over 3 months
    forecast_6m_usd:      float   # projected saving over 6 months
    forecast_12m_usd:     float   # projected saving over 12 months
    compression_ratio:    float   # only meaningful for compress verdicts
    note:                 str     # human-readable explanation


@dataclass
class BatchForecast:
    """
    Aggregate forecast across all actionable decisions in a batch run.
    """
    actionable_count:     int     # number of DELETE or COMPRESS verdicts
    total_monthly_usd:    float   # combined monthly saving if all actioned
    total_agent_cost_usd: float   # total agent cost for the batch
    break_even_months:    float   # batch-level break-even
    forecast_3m_usd:      float
    forecast_6m_usd:      float
    forecast_12m_usd:     float


def compute_decision_forecast(
    verdict: str,
    size_bytes: int,
    storage_cost_per_gb_day: float,
    agent_cost_usd: float,
) -> DecisionForecast:
    """
    Compute a savings forecast for a single decision.

    For DELETE verdicts: full storage saving every month.
    For COMPRESS verdicts: saving = cost of data we no longer store
        (original size × compression_ratio reduction).
    For KEEP/STANDDOWN: no saving, forecast is zero.
    """
    size_gb = size_bytes / 1024 / 1024 / 1024

    if verdict == "delete":
        # Full saving — entire conversation is removed
        monthly_saving = size_gb * storage_cost_per_gb_day * 30
        note = (
            "If deleted, the full storage cost is eliminated each month. "
            "Agent cost was a one-time expense already paid."
        )
        comp_ratio = 1.0

    elif verdict == "compress":
        # Partial saving — compressed version retained at 30% of original size
        saved_fraction = 1.0 - COMPRESSION_RATIO
        monthly_saving = size_gb * saved_fraction * storage_cost_per_gb_day * 30
        note = (
            f"If compressed to ~{int(COMPRESSION_RATIO * 100)}% of original size, "
            f"{int(saved_fraction * 100)}% of monthly storage cost is eliminated. "
            "Content remains accessible in compressed form."
        )
        comp_ratio = COMPRESSION_RATIO

    else:
        # KEEP, STANDDOWN — no actionable saving
        return DecisionForecast(
            verdict=verdict,
            monthly_saving_usd=0.0,
            agent_cost_usd=agent_cost_usd,
            break_even_months=0.0,
            forecast_3m_usd=0.0,
            forecast_6m_usd=0.0,
            forecast_12m_usd=0.0,
            compression_ratio=1.0,
            note="No storage saving — conversation retained as-is.",
        )

    # Break-even: how many months until recurring savings cover agent cost?
    break_even = (
        agent_cost_usd / monthly_saving
        if monthly_saving > 0
        else float("inf")
    )

    return DecisionForecast(
        verdict=verdict,
        monthly_saving_usd=round(monthly_saving, 8),
        agent_cost_usd=round(agent_cost_usd, 8),
        break_even_months=round(min(break_even, 9999), 2),
        forecast_3m_usd=round(monthly_saving * 3, 6),
        forecast_6m_usd=round(monthly_saving * 6, 6),
        forecast_12m_usd=round(monthly_saving * 12, 6),
        compression_ratio=comp_ratio,
        note=note,
    )


def compute_batch_forecast(
    decisions: list[dict],
    storage_cost_per_gb_day: float,
) -> BatchForecast:
    """
    Aggregate forecast across all actionable decisions in a batch.
    Each decision dict must have: verdict, size_bytes, agent_cost_usd.
    Only DELETE and COMPRESS verdicts are counted as actionable.
    """
    actionable = [
        d for d in decisions
        if d.get("verdict") in ("delete", "compress")
    ]

    if not actionable:
        return BatchForecast(
            actionable_count=0,
            total_monthly_usd=0.0,
            total_agent_cost_usd=sum(d.get("agent_cost_usd", 0) for d in decisions),
            break_even_months=0.0,
            forecast_3m_usd=0.0,
            forecast_6m_usd=0.0,
            forecast_12m_usd=0.0,
        )

    total_monthly    = 0.0
    total_agent_cost = 0.0

    for d in actionable:
        forecast = compute_decision_forecast(
            verdict=d["verdict"],
            size_bytes=d["size_bytes"],
            storage_cost_per_gb_day=storage_cost_per_gb_day,
            agent_cost_usd=d.get("agent_cost_usd", 0),
        )
        total_monthly    += forecast.monthly_saving_usd
        total_agent_cost += d.get("agent_cost_usd", 0)

    break_even = (
        total_agent_cost / total_monthly
        if total_monthly > 0
        else 0.0
    )

    return BatchForecast(
        actionable_count=len(actionable),
        total_monthly_usd=round(total_monthly, 6),
        total_agent_cost_usd=round(total_agent_cost, 6),
        break_even_months=round(min(break_even, 9999), 2),
        forecast_3m_usd=round(total_monthly * 3, 4),
        forecast_6m_usd=round(total_monthly * 6, 4),
        forecast_12m_usd=round(total_monthly * 12, 4),
    )
