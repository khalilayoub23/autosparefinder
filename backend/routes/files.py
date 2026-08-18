"""Files — all /api/v1/files* endpoints extracted from BACKEND_API_ROUTES.py."""

import os
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import Response, HTMLResponse
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from BACKEND_DATABASE_MODELS import get_pii_db, User, File as FileModel
from BACKEND_AUTH_SECURITY import get_current_user, get_current_verified_user
from routes.utils import _scan_bytes_for_virus

router = APIRouter()

_UPLOADS_DIR = Path("/app/uploads")

# ── WhatsApp QR code live page (temp, no auth — for device pairing) ──
# wa_qr_raw.txt is synced from the whatsapp-bridge container by the host-side qr_sync loop
_WA_QR_TXT = _UPLOADS_DIR / "wa_qr_raw.txt"
_WA_QR_PNG = _UPLOADS_DIR / "wa_qr.png"

def _refresh_wa_qr_png() -> bool:
    """Regenerate wa_qr.png from the bridge's current qr.txt. Returns True on success."""
    try:
        import qrcode as _qrcode
        data = _WA_QR_TXT.read_text().strip()
        if not data:
            return False
        qr = _qrcode.QRCode(version=None, error_correction=_qrcode.constants.ERROR_CORRECT_L, box_size=12, border=4)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(str(_WA_QR_PNG))
        return True
    except Exception:
        return False

@router.get("/api/v1/whatsapp-qr")
async def whatsapp_qr_page():
    """Auto-refreshing QR page for WhatsApp device pairing. Remove after pairing."""
    _refresh_wa_qr_png()
    html = """<!doctype html><html><head><meta charset=utf-8>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WhatsApp QR — AutoSpareFinder</title>
<meta http-equiv="refresh" content="18">
<style>body{background:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;margin:0;font-family:sans-serif}
img{max-width:360px;width:90vw;border:4px solid #25D366;border-radius:8px}
p{color:#555;margin-top:16px;font-size:14px;text-align:center}</style>
</head><body>
<h2 style="color:#25D366">WhatsApp Pairing QR</h2>
<img src="/api/v1/whatsapp-qr-img" alt="QR Code">
<p>Refreshes automatically every 18 seconds.<br>Open WhatsApp → Linked Devices → Link a Device → scan this QR.</p>
</body></html>"""
    return HTMLResponse(html)

@router.get("/api/v1/whatsapp-qr-img")
async def whatsapp_qr_img():
    """Serve the current WhatsApp QR as a PNG image."""
    _refresh_wa_qr_png()
    if not _WA_QR_PNG.exists():
        raise HTTPException(status_code=503, detail="QR not ready yet")
    data = _WA_QR_PNG.read_bytes()
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"})

# ── Public demo video download (no auth — used for TikTok review submission) ──
@router.get("/api/v1/download/tiktok-demo")
async def download_tiktok_demo():
    """Serve the TikTok integration demo video for review submission."""
    path = _UPLOADS_DIR / "tiktok_demo.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Demo video not found")
    data = path.read_bytes()
    return Response(
        content=data,
        media_type="video/mp4",
        headers={
            "Content-Disposition": 'attachment; filename="autosparefinder_tiktok_demo.mp4"',
            "Cache-Control": "public, max-age=3600",
            "Content-Length": str(len(data)),
        },
    )

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
