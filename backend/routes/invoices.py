"""Invoices - all /api/v1/invoices/* endpoints extracted from BACKEND_API_ROUTES.py."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import EmailStr
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from BACKEND_AUTH_SECURITY import get_current_user
from BACKEND_DATABASE_MODELS import Invoice, User, get_pii_db

router = APIRouter()


@router.get("/api/v1/invoices")
async def get_invoices(current_user: User = Depends(get_current_user), limit: int = 50, db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Invoice).where(Invoice.user_id == current_user.id).order_by(Invoice.issued_at.desc()).limit(limit))
    invoices = result.scalars().all()
    return {"invoices": [{"id": str(i.id), "invoice_number": i.invoice_number, "order_id": str(i.order_id), "pdf_url": i.pdf_url, "issued_at": i.issued_at} for i in invoices]}


@router.get("/api/v1/invoices/{invoice_id}")
async def get_invoice(invoice_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Invoice).where(and_(Invoice.id == invoice_id, Invoice.user_id == current_user.id)))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"id": str(invoice.id), "invoice_number": invoice.invoice_number, "pdf_url": invoice.pdf_url, "business_number": invoice.business_number, "issued_at": invoice.issued_at}


@router.get("/api/v1/invoices/{invoice_id}/download")
async def download_invoice(invoice_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Invoice).where(and_(Invoice.id == invoice_id, Invoice.user_id == current_user.id)))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"download_url": invoice.pdf_url}


@router.post("/api/v1/invoices/{invoice_id}/resend")
async def resend_invoice(invoice_id: str, email: Optional[EmailStr] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(Invoice).where(and_(Invoice.id == invoice_id, Invoice.user_id == current_user.id)))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"message": f"Invoice sent to {email or current_user.email}"}
