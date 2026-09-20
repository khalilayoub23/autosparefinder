"""
Script: routes/noa_ops_routes.py
Purpose: Read-only observability endpoint for NOA's two-phase operational readiness
         (2026-09-20): the 12-point autonomous-release readiness report plus 24h / 7d /
         30d engagement metrics, evaluated INSIDE the app process (readiness check 11 needs
         the supervised-task registry). It never changes any flag - releasing autonomous mode
         is a deliberate operator action (NOA_ENGAGEMENT_AUTOREPLY=1), not something this
         endpoint or the readiness check can do.
Process: GET /api/v1/system/noa-ops  (header X-Collect-Secret, fail-closed).
Data Imported/Modified: none (reads noa_scan_runs / group_comment_drafts / social_inbox).
Data Sources: catalog DB.
Last Updated: 2026-09-20
"""
import hmac
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Header, HTTPException

router = APIRouter()


@router.get("/api/v1/system/noa-ops")
async def noa_ops_status(x_collect_secret: str = Header(default="")):
    secret = os.getenv("COLLECT_SECRET", "")
    if not secret or not hmac.compare_digest(x_collect_secret.encode(), secret.encode()):
        raise HTTPException(status_code=403, detail="forbidden")
    from BACKEND_DATABASE_MODELS import async_session_factory
    from social import noa_ops
    now = datetime.utcnow()
    async with async_session_factory() as db:
        rep = await noa_ops.readiness_report(db)
        metrics = {label: await noa_ops.daily_metrics(db, now - timedelta(hours=h))
                   for label, h in (("24h", 24), ("7d", 24 * 7), ("30d", 24 * 30))}
    return {
        "autonomous_mode": "ON" if noa_ops.autonomous_flag_on() else "OFF",
        "readiness": rep,
        "metrics": metrics,
    }
