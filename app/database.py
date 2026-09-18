"""
Supabase Telemetry & History Module.
Provides optional persistent storage for optimization runs.
Executed asynchronously as a background task to ensure zero impact on endpoint latency.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

from app.config import SUPABASE_ANON_KEY, SUPABASE_URL
from app.logging_utils import logger

# In-memory fallback history if Supabase is unavailable
_IN_MEMORY_HISTORY: List[Dict[str, Any]] = []


async def record_optimization_run(
    scenario_id: str,
    operator_notes: List[str],
    total_grid_kwh: float,
    total_cost_bdt: float,
    peak_grid_kwh: float,
    applied_directives_count: int,
    execution_time_seconds: float,
) -> None:
    """
    Records an optimization run to Supabase and in-memory cache.
    Non-blocking, fire-and-forget: failure to log never affects the API response.
    """
    entry = {
        "scenario_id": scenario_id,
        "notes_count": len(operator_notes),
        "total_grid_kwh": total_grid_kwh,
        "total_cost_bdt": total_cost_bdt,
        "peak_grid_kwh": peak_grid_kwh,
        "applied_directives_count": applied_directives_count,
        "execution_time_seconds": round(execution_time_seconds, 3),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Always keep in memory history (max 50)
    _IN_MEMORY_HISTORY.insert(0, entry)
    if len(_IN_MEMORY_HISTORY) > 50:
        _IN_MEMORY_HISTORY.pop()

    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return

    try:
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/optimization_runs"
        headers = {
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(url, json=entry, headers=headers)
            if resp.status_code in (200, 201):
                logger.debug(f"Successfully recorded scenario '{scenario_id}' to Supabase")
            else:
                logger.debug(f"Supabase telemetry returned status {resp.status_code} (table may not exist yet)")
    except Exception as e:
        logger.debug(f"Supabase telemetry skipped: {type(e).__name__}")


def get_recent_history() -> List[Dict[str, Any]]:
    """Returns recent optimization history."""
    return list(_IN_MEMORY_HISTORY)
