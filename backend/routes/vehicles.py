"""
Vehicles — all /api/v1/vehicles/* endpoints extracted from BACKEND_API_ROUTES.py.

Endpoints:
  POST   /api/v1/vehicles/identify
  POST   /api/v1/vehicles/identify-from-image
  GET    /api/v1/vehicles/my-vehicles
  POST   /api/v1/vehicles/my-vehicles
  PUT    /api/v1/vehicles/my-vehicles/{vehicle_id}
  DELETE /api/v1/vehicles/my-vehicles/{vehicle_id}
  POST   /api/v1/vehicles/my-vehicles/set-primary
  GET    /api/v1/vehicles/{vehicle_id}/compatible-parts
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel, Field
import base64 as _b64
import os
import re

from BACKEND_DATABASE_MODELS import get_db, get_pii_db, User, Vehicle, UserVehicle
from BACKEND_AUTH_SECURITY import (
    get_redis, check_rate_limit, get_current_user, get_current_verified_user,
)
from BACKEND_AI_AGENTS import get_agent

router = APIRouter()


class VehicleIdentifyRequest(BaseModel):
    license_plate: str = Field(..., max_length=15)


# ==============================================================================
# POST /api/v1/vehicles/identify
# ==============================================================================

@router.post("/api/v1/vehicles/identify")
async def identify_vehicle(data: VehicleIdentifyRequest, db: AsyncSession = Depends(get_pii_db), request: Request = None, redis=Depends(get_redis)):
    if redis and request:
        _iv_ip = request.client.host if request.client else "anon"
        await check_rate_limit(redis, f"identify_vehicle:{_iv_ip}", 10, 60)
    agent = get_agent("parts_finder_agent")
    try:
        result = await agent.identify_vehicle(data.license_plate, db)
    except Exception as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=f"לוחית רישוי {data.license_plate} לא נמצאה במאגר משרד התחבורה")
        raise HTTPException(status_code=502, detail=f"שגיאה בקריאת מאגר הרכבים: {msg}")
    return result


# ==============================================================================
# POST /api/v1/vehicles/identify-from-image
# ==============================================================================

@router.post("/api/v1/vehicles/identify-from-image")
async def identify_vehicle_from_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
    request: Request = None,
    redis=Depends(get_redis),
):
    """Extract license plate from image via HF vision OCR, then look up vehicle via data.gov.il"""
    if redis and request:
        ip = request.client.host if request.client else 'unknown'
        allowed = await check_rate_limit(redis, f'rate:identify_img:{ip}', 10, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')

    img_bytes = await file.read()
    if len(img_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail='Image too large (max 10 MB)')
    _ALLOWED = {'image/jpeg', 'image/png', 'image/webp'}
    if (file.content_type or '').split(';')[0].strip() not in _ALLOWED:
        raise HTTPException(status_code=415, detail='Unsupported image type')

    if not os.getenv("HF_TOKEN", ""):
        raise HTTPException(status_code=503, detail='ocr_service_unavailable')

    from hf_client import hf_vision
    b64 = _b64.b64encode(img_bytes).decode()
    try:
        plate_raw = await hf_vision(
            b64,
            'Extract the Israeli license plate number from this image. Return ONLY the digits and dashes, for example: 123-45-678. If no plate is visible return empty string.',
            mime=(file.content_type or "image/jpeg"),
        )
        plate_raw = plate_raw.strip()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f'ocr_failed: {str(e)[:100]}')

    plate = re.sub(r'[^0-9\-]', '', plate_raw).strip('-')
    if not plate or len(plate) < 5:
        raise HTTPException(status_code=422, detail='no_plate_detected')

    agent = get_agent('parts_finder_agent')
    try:
        result = await agent.identify_vehicle(plate, db)
        result['plate_extracted'] = plate
        result['ocr_raw'] = plate_raw
        return result
    except Exception as e:
        raise HTTPException(status_code=404, detail=f'vehicle_not_found: {str(e)[:100]}')


# ==============================================================================
# GET /api/v1/vehicles/my-vehicles
# ==============================================================================

@router.get("/api/v1/vehicles/my-vehicles")
async def get_my_vehicles(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
    catalog_db: AsyncSession = Depends(get_db),
):
    # Step 1: fetch UserVehicle rows from PII DB
    uv_result = await db.execute(
        select(UserVehicle).where(UserVehicle.user_id == current_user.id)
    )
    user_vehicles = uv_result.scalars().all()

    if not user_vehicles:
        return {"vehicles": []}

    # Step 2: fetch Vehicle details from catalog DB (separate database)
    vehicle_ids = [uv.vehicle_id for uv in user_vehicles]
    v_result = await catalog_db.execute(
        select(Vehicle).where(Vehicle.id.in_(vehicle_ids))
    )
    vehicle_map = {v.id: v for v in v_result.scalars().all()}

    vehicles = []
    for uv in user_vehicles:
        v = vehicle_map.get(uv.vehicle_id)
        if not v:
            continue
        gov = v.gov_api_data or {}
        vehicles.append({
            "id": str(v.id),
            "nickname": uv.nickname,
            "is_primary": uv.is_primary,
            "license_plate": v.license_plate,
            "manufacturer": v.manufacturer,
            "model": v.model,
            "year": v.year,
            "engine_type": v.engine_type,
            "fuel_type": v.fuel_type or gov.get("fuel_type"),
            "color": gov.get("color"),
            "transmission": v.transmission or gov.get("transmission"),
            "engine_cc": gov.get("engine_cc"),
            "horsepower": gov.get("horsepower"),
            "vehicle_type": gov.get("vehicle_type"),
            "doors": gov.get("doors"),
            "seats": gov.get("seats"),
            "front_tire": gov.get("front_tire"),
            "rear_tire": gov.get("rear_tire"),
            "emissions_group": gov.get("emissions_group"),
            "last_test_date": gov.get("last_test_date"),
            "test_expiry_date": gov.get("test_expiry_date"),
            "ownership": gov.get("ownership"),
            "country_of_origin": gov.get("country_of_origin"),
        })
    return {"vehicles": vehicles}


# ==============================================================================
# POST /api/v1/vehicles/my-vehicles
# ==============================================================================

@router.post("/api/v1/vehicles/my-vehicles")
async def add_my_vehicle(license_plate: str = Form(...), nickname: Optional[str] = Form(None), current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    agent = get_agent("parts_finder_agent")
    try:
        vehicle_data = await agent.identify_vehicle(license_plate, db)
    except Exception as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=f"לוחית רישוי {license_plate} לא נמצאה במאגר משרד התחבורה")
        raise HTTPException(status_code=502, detail=f"שגיאה בקריאת מאגר הרכבים: {msg}")
    db.add(UserVehicle(user_id=current_user.id, vehicle_id=vehicle_data["id"], nickname=nickname, is_primary=False))
    await db.commit()
    return {"message": "Vehicle added", "vehicle": vehicle_data}


# ==============================================================================
# PUT /api/v1/vehicles/my-vehicles/{vehicle_id}
# ==============================================================================

@router.put("/api/v1/vehicles/my-vehicles/{vehicle_id}")
async def update_my_vehicle(vehicle_id: str, nickname: Optional[str] = None, is_primary: Optional[bool] = None, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(UserVehicle).where(and_(UserVehicle.vehicle_id == vehicle_id, UserVehicle.user_id == current_user.id)))
    uv = result.scalar_one_or_none()
    if not uv:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    if nickname is not None:
        uv.nickname = nickname
    if is_primary is not None:
        uv.is_primary = is_primary
    await db.commit()
    return {"message": "Vehicle updated"}


# ==============================================================================
# DELETE /api/v1/vehicles/my-vehicles/{vehicle_id}
# ==============================================================================

@router.delete("/api/v1/vehicles/my-vehicles/{vehicle_id}")
async def delete_my_vehicle(vehicle_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(UserVehicle).where(and_(UserVehicle.vehicle_id == vehicle_id, UserVehicle.user_id == current_user.id)))
    uv = result.scalar_one_or_none()
    if not uv:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    await db.delete(uv)
    await db.commit()
    return {"message": "Vehicle removed"}


# ==============================================================================
# POST /api/v1/vehicles/my-vehicles/set-primary
# ==============================================================================

@router.post("/api/v1/vehicles/my-vehicles/set-primary")
async def set_primary_vehicle(vehicle_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(UserVehicle).where(UserVehicle.user_id == current_user.id))
    for uv in result.scalars().all():
        uv.is_primary = (str(uv.vehicle_id) == vehicle_id)
    await db.commit()
    return {"message": "Primary vehicle updated"}


# ==============================================================================
# GET /api/v1/vehicles/{vehicle_id}/compatible-parts
# ==============================================================================

@router.get("/api/v1/vehicles/{vehicle_id}/compatible-parts")
async def get_compatible_parts(vehicle_id: str, category: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    return {"parts": [], "message": "Compatibility filter coming soon"}
