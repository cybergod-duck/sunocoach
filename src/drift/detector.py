import os
import statistics
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from db.client import fetchrow, fetch, execute

DRIFT_THRESHOLD = 0.20  # 20% drop triggers drift


def _median_and_iqr(values: List[float]) -> tuple:
    """Calculate median and IQR for outlier rejection."""
    if len(values) < 4:
        return statistics.median(values), 0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    q1 = sorted_vals[n // 4] if n >= 4 else sorted_vals[0]
    q3 = sorted_vals[3 * n // 4] if n >= 4 else sorted_vals[-1]
    iqr = q3 - q1
    median = statistics.median(sorted_vals)
    return median, iqr


def _reject_outliers(values: List[float]) -> List[float]:
    """Reject values outside 1.5*IQR."""
    if len(values) < 4:
        return values
    median, iqr = _median_and_iqr(values)
    lower = median - 1.5 * iqr
    upper = median + 1.5 * iqr
    return [v for v in values if lower <= v <= upper]


async def check_drift_status() -> Optional[Dict[str, Any]]:
    """Check if active workflow pattern is drifting."""
    # Get last 48 hours ratings
    recent_rows = await fetch(
        "SELECT ss.quality_rating "
        "FROM session_steps ss "
        "JOIN sessions s ON ss.session_id = s.id "
        "WHERE ss.created_at > NOW() - INTERVAL '48 hours' "
        "AND s.status = 'completed'"
    )

    if not recent_rows or len(recent_rows) < 5:
        return None  # Not enough data

    recent_vals = [float(r["quality_rating"]) for r in recent_rows]
    recent_clean = _reject_outliers(recent_vals)
    recent_avg = statistics.mean(recent_clean) if recent_clean else statistics.mean(recent_vals)

    # Get 7-day baseline (excluding last 48h)
    baseline_rows = await fetch(
        "SELECT ss.quality_rating "
        "FROM session_steps ss "
        "JOIN sessions s ON ss.session_id = s.id "
        "WHERE ss.created_at BETWEEN NOW() - INTERVAL '7 days' AND NOW() - INTERVAL '48 hours' "
        "AND s.status = 'completed'"
    )

    if not baseline_rows or len(baseline_rows) < 10:
        return None  # Not enough baseline data

    baseline_vals = [float(r["quality_rating"]) for r in baseline_rows]
    baseline_clean = _reject_outliers(baseline_vals)
    baseline_avg = statistics.mean(baseline_clean) if baseline_clean else statistics.mean(baseline_vals)

    if baseline_avg == 0:
        return None

    drop = (baseline_avg - recent_avg) / baseline_avg

    if drop > DRIFT_THRESHOLD:
        # Check if drift event already recorded in last 48h
        existing = await fetchrow(
            "SELECT id FROM drift_events WHERE detected_at > NOW() - INTERVAL '48 hours' AND resolved_at IS NULL"
        )

        if not existing:
            # Create drift event
            await execute(
                "INSERT INTO drift_events (sessions_sampled, avg_score_before, avg_score_after) "
                "VALUES ($1, $2, $3)",
                len(recent_rows), baseline_avg, recent_avg
            )

            # Update active pattern status
            await execute(
                "UPDATE workflow_patterns SET status = 'drifting' WHERE status = 'active'"
            )

        return {
            "detected": True,
            "warning": (
                f"DRIFT DETECTED: Workflow pattern quality dropped {drop*100:.1f}% "
                f"(from {baseline_avg:.2f} to {recent_avg:.2f}). "
                f"Community contributors: submit new patterns now."
            ),
            "baseline_avg": baseline_avg,
            "recent_avg": recent_avg,
            "drop_percent": round(drop * 100, 1),
            "sessions_sampled": len(recent_rows)
        }

    return {"detected": False}


async def resolve_drift(pattern_id: str) -> Dict[str, Any]:
    """Mark drift as resolved when a new pattern takes over."""
    await execute(
        "UPDATE drift_events SET resolved_at = NOW(), resolved_by_pattern_id = $1 "
        "WHERE resolved_at IS NULL",
        pattern_id
    )

    # Deprecate old active, activate new
    await execute(
        "UPDATE workflow_patterns SET status = 'deprecated' WHERE status = 'drifting'"
    )
    await execute(
        "UPDATE workflow_patterns SET status = 'active' WHERE id = $1",
        pattern_id
    )

    return {"resolved": True, "new_active_pattern": pattern_id}
