"""Files — all /api/v1/files* endpoints extracted from BACKEND_API_ROUTES.py."""

import os
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from BACKEND_DATABASE_MODELS import get_pii_db, User, File as FileModel
from BACKEND_AUTH_SECURITY import get_current_user, get_current_verified_user
from routes.utils import _scan_bytes_for_virus

router = APIRouter()

@router.post("/api/v1/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
):
    allowed = ["image/jpeg", "image/png", "image/webp", "audio/mpeg", "audio/wav", "video/mp4"]
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="File type not allowed")
    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 25MB)")
    # Virus scan before persisting anything
    scan_status, virus_name = _scan_bytes_for_virus(content)
    if scan_status == "infected":
        raise HTTPException(status_code=400, detail=f"File rejected: malware detected ({virus_name})")
    stored_filename = f"{uuid.uuid4()}_{file.filename}"
    ftype = "image" if "image" in (file.content_type or "") else ("audio" if "audio" in (file.content_type or "") else "video")
    file_record = FileModel(
        user_id=current_user.id,
        original_filename=file.filename,
        stored_filename=stored_filename,
        file_type=ftype,
        mime_type=file.content_type,
        file_size_bytes=len(content),
        storage_path=f"/uploads/{stored_filename}",
        expires_at=datetime.utcnow() + timedelta(days=30),
        virus_scan_status=scan_status,
        virus_scan_at=datetime.utcnow() if scan_status != "skipped" else None,
    )
    db.add(file_record)
    await db.commit()
    await db.refresh(file_record)
    return {"file_id": str(file_record.id), "url": f"/api/v1/files/{file_record.id}", "expires_at": file_record.expires_at}


@router.get("/api/v1/files/{file_id}")
async def get_file(
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
):
    result = await db.execute(select(FileModel).where(and_(FileModel.id == file_id, FileModel.user_id == current_user.id)))
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    return {
        "id": str(f.id),
        "filename": f.original_filename,
        "file_type": f.file_type,
        "size_bytes": f.file_size_bytes,
        "url": f.cdn_url or f.storage_path,
        "expires_at": f.expires_at,
    }


@router.delete("/api/v1/files/{file_id}")
async def delete_file(
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
):
    result = await db.execute(select(FileModel).where(and_(FileModel.id == file_id, FileModel.user_id == current_user.id)))
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    f.deleted_at = datetime.utcnow()
    await db.commit()
    return {"message": "File deleted"}
