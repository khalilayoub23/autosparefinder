from fastapi import FastAPI, HTTPException
from typing import List

app = FastAPI()

@app.get("/health", tags=["health"])
async def health_check():
    """Lightweight health endpoint for container/monitoring (returns DB status if available)."""
    status = {"app": "ok"}
    try:
        # optional quick DB ping (if dependencies available)
        from ..dependencies import engine
        with engine.connect() as conn:
            conn.execute("SELECT 1")
        status["db"] = "ok"
    except Exception:
        status["db"] = "unavailable"
    return {"status": status}

@app.get("/parts/{part_id}")
async def get_part(part_id: str):
    return {"part_id": part_id}

@app.get("/suppliers")
async def list_suppliers():
    return {"suppliers": []}

@app.post("/parts/scan")
async def scan_barcode(barcode_data: str):
    return {"status": "scanned", "barcode": barcode_data}

from fastapi import Depends
from ..inventory import InventoryManager
from ..dependencies import SessionLocal

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/alerts")
async def get_alerts(db: Session = Depends(get_db)):
    inventory = InventoryManager(db)
    return inventory.get_active_alerts()
