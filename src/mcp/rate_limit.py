import os
import time
from typing import Dict, Optional
try:
    from fastapi import Request, HTTPException
except ImportError:
    from utils.http_compat import HTTPException
    Request = None

# In-memory rate limit store (replace with Redis/Upstash in production)
_rate_store: Dict[str, Dict] = {}

FREE_MONTHLY_LIMIT = 10
CONTRIBUTOR_DAILY_SUBMISSIONS = 3


async def check_rate_limit(request: Request, user_id: str, tier: str, action: str = "session") -> None:
    """Check and enforce rate limits based on tier."""

    if tier == "pro":
        return  # No limits for Pro

    if tier == "free" and action == "session":
        key = f"sessions:{user_id}:{_current_month()}"
        current = _rate_store.get(key, {"count": 0})

        if current["count"] >= FREE_MONTHLY_LIMIT:
            raise HTTPException(
                status_code=403,
                detail=f"Free tier limit reached ({FREE_MONTHLY_LIMIT} sessions/month). Upgrade to Pro."
            )

        current["count"] = current.get("count", 0) + 1
        _rate_store[key] = current

    elif tier == "contributor" and action == "submit":
        key = f"submissions:{user_id}:{_current_day()}"
        current = _rate_store.get(key, {"count": 0})

        if current["count"] >= CONTRIBUTOR_DAILY_SUBMISSIONS:
            raise HTTPException(
                status_code=429,
                detail=f"Daily submission limit reached ({CONTRIBUTOR_DAILY_SUBMISSIONS}/day)."
            )

        current["count"] = current.get("count", 0) + 1
        _rate_store[key] = current


def _current_month() -> str:
    return time.strftime("%Y-%m")


def _current_day() -> str:
    return time.strftime("%Y-%m-%d")


def get_usage_stats(user_id: str, tier: str) -> Dict:
    """Get current usage stats for a user."""
    if tier == "pro":
        return {"tier": "pro", "unlimited": True}

    if tier == "free":
        key = f"sessions:{user_id}:{_current_month()}"
        current = _rate_store.get(key, {"count": 0})
        return {
            "tier": "free",
            "sessions_used": current["count"],
            "sessions_limit": FREE_MONTHLY_LIMIT,
            "remaining": max(0, FREE_MONTHLY_LIMIT - current["count"])
        }

    if tier == "contributor":
        key = f"submissions:{user_id}:{_current_day()}"
        current = _rate_store.get(key, {"count": 0})
        return {
            "tier": "contributor",
            "submissions_today": current["count"],
            "submissions_limit": CONTRIBUTOR_DAILY_SUBMISSIONS,
            "remaining": max(0, CONTRIBUTOR_DAILY_SUBMISSIONS - current["count"])
        }

    return {"tier": tier}
