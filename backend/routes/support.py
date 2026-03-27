"""
Support & Bug Reports — extracted from BACKEND_API_ROUTES.py.

Endpoints:
  POST /api/v1/support/report              (public — optional auth)
  GET  /api/v1/admin/bug-reports           (admin)
  PUT  /api/v1/admin/bug-reports/{report_id} (admin)
"""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from BACKEND_DATABASE_MODELS import get_db, get_pii_db, User, BugReport, ApprovalQueue
from BACKEND_AUTH_SECURITY import get_current_user, get_current_admin_user
from BACKEND_AI_AGENTS import TechAgent

router = APIRouter()


@router.post("/api/v1/support/report")
async def submit_bug_report(
    request: Request,
    db: AsyncSession = Depends(get_db),
    pii_db: AsyncSession = Depends(get_pii_db),
):
    body = await request.json()
    user = None
    try:
        user = await get_current_user(request, pii_db)
    except Exception:
        pass

    lang = request.headers.get("accept-language", "he")[:2]
    device_info = {
        "user_agent": request.headers.get("user-agent", ""),
        "platform": request.headers.get("x-platform", body.get("platform", "web")),
        "app_version": request.headers.get("x-app-version", body.get("app_version", "")),
        "language": lang,
    }

    report_data = {
        "title": body.get("title", "Bug Report"),
        "description": body.get("description", ""),
        "platform": device_info["platform"],
        "app_version": device_info["app_version"],
        "screen_name": body.get("screen_name"),
        "endpoint_url": body.get("endpoint_url"),
        "http_method": body.get("http_method"),
        "http_status_code": body.get("http_status_code"),
        "error_trace": body.get("error_trace"),
        "last_api_calls": body.get("last_api_calls", []),
    }

    tech_agent = TechAgent()
    analysis = await tech_agent.process({"report": report_data})

    report = BugReport(
        id=uuid.uuid4(),
        user_id=user.id if user else None,
        user_role=getattr(user, "role", None),
        tech_analysis=analysis,
        device_info=device_info,
        severity=analysis.get("severity", "medium"),
        **{k: v for k, v in report_data.items()},
    )
    db.add(report)
    await db.flush()

    if analysis.get("severity") in ("critical", "high"):
        approval = ApprovalQueue(
            id=uuid.uuid4(),
            entity_type="bug_report",
            entity_id=report.id,
            action="review_bug_report",
            payload={
                "bug_report_id": str(report.id),
                "title": report_data["title"],
                "severity": analysis.get("severity"),
                "affected_component": analysis.get("affected_component", ""),
                "suggested_fix": analysis.get("suggested_fix", ""),
            },
            status="pending",
            requested_by=user.id if user else None,
        )
        pii_db.add(approval)

    await db.commit()
    await pii_db.commit()

    msg_key = f"customer_message_{lang}" if lang in ("he", "ar", "en") else "customer_message_he"
    message = analysis.get(msg_key, analysis.get("customer_message_he", "קיבלנו את הדיווח"))

    return {
        "success": True,
        "report_id": str(report.id),
        "message": message,
        "severity": analysis.get("severity", "medium"),
    }


@router.get("/api/v1/admin/bug-reports")
async def list_bug_reports(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    query = select(BugReport).order_by(BugReport.created_at.desc())
    if status:
        query = query.where(BugReport.status == status)
    if severity:
        query = query.where(BugReport.severity == severity)
    result = await db.execute(query.limit(limit).offset(offset))
    reports = result.scalars().all()
    return {
        "reports": [
            {
                "id": str(r.id),
                "title": r.title,
                "severity": r.severity,
                "status": r.status,
                "platform": r.platform,
                "screen_name": r.screen_name,
                "endpoint_url": r.endpoint_url,
                "tech_analysis": r.tech_analysis,
                "admin_notes": r.admin_notes,
                "created_at": str(r.created_at),
                "resolved_at": str(r.resolved_at) if r.resolved_at else None,
            }
            for r in reports
        ],
        "total": len(reports),
    }


@router.put("/api/v1/admin/bug-reports/{report_id}")
async def update_bug_report(
    report_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    body = await request.json()
    try:
        report_uuid = uuid.UUID(report_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid report_id") from exc

    result = await db.execute(select(BugReport).where(BugReport.id == report_uuid))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Bug report not found")
    if "status" in body:
        report.status = body["status"]
        if body["status"] == "resolved":
            report.resolved_at = datetime.utcnow()
    if "admin_notes" in body:
        report.admin_notes = body["admin_notes"]
    report.updated_at = datetime.utcnow()
    await db.commit()
    return {"success": True, "report_id": report_id, "status": report.status}
