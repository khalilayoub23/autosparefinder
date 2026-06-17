#!/usr/bin/env python3
"""
Import parts collected from fridayparts.com directly into parts_catalog.
Run inside backend container: python3 fridayparts_seed_import.py

Handles multi-OEM-number parts (splits on comma/slash, uses first as primary).
Skips parts with no OEM number (N/A).
"""
from __future__ import annotations
import asyncio, hashlib, json, logging, re, sys, uuid
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)
USD_TO_ILS = 3.65

# Real IDs from car_brands table
BRAND_IDS = {
    "Isuzu": "a5f0f44e-814d-4fa2-b6b6-dd1b3175d855",
}

CATEGORY_MAP = {
    "engine": "engine", "engine components": "engine", "engine parts": "engine",
    "engine rebuild": "engine", "engine block": "engine", "engine bearings": "engine",
    "engine fasteners": "engine", "engine gaskets": "engine", "cylinder head": "engine",
    "fuel system": "fuel-air", "fuel pump": "fuel-air", "fuel pumps": "fuel-air",
    "fuel injectors": "fuel-air", "fuel injector": "fuel-air", "carburetors": "fuel-air",
    "filters": "engine", "filtration": "engine", "filter": "engine",
    "cooling": "cooling", "cooling system": "cooling", "cooling kits": "cooling",
    "turbo system": "engine", "turbocharger": "engine", "turbocharging": "engine",
    "turbo components": "engine",
    "electrical": "electrical-sensors", "sensors": "electrical-sensors",
    "sensor": "electrical-sensors", "engine control": "electrical-sensors",
    "emissions": "electrical-sensors",
    "brakes": "brakes", "brake system": "brakes", "braking system": "brakes",
    "suspension": "suspension-steering", "suspension/brakes": "suspension-steering",
    "suspension/wheels": "wheels-bearings",
    "steering": "suspension-steering",
    "transmission": "gearbox", "clutch": "clutch-drivetrain",
    "gaskets": "engine", "gasket": "engine", "gasket kit": "engine",
    "rebuild kits": "engine", "timing components": "engine",
    "belts": "belts-chains", "belt & pulley": "belts-chains",
    "vacuum system": "engine",
    "hvac": "air-conditioning-heating", "climate control": "air-conditioning-heating",
    "body parts": "body-exterior", "accessories": "accessories",
    "wheels": "wheels-bearings", "solenoids": "electrical-sensors",
    "bearing": "engine", "bearings": "engine",
    "bolt": "engine", "liner": "engine", "gear": "engine",
    "nozzle": "fuel-air", "valve": "engine",
    "lock set": "body-exterior", "pump": "fuel-air",
    "crankshaft": "engine",
}


def map_cat(raw: str) -> str:
    k = raw.lower().strip()
    return CATEGORY_MAP.get(k, "accessories")


def clean_oem(raw: str) -> str:
    """Return first clean OEM number from a comma/slash-separated string."""
    if not raw or raw.strip() in ("N/A", "n/a", ""):
        return ""
    nums = re.split(r"[,/]", raw)
    first = nums[0].strip()
    first = re.sub(r"\s+", "", first)
    return first if len(first) >= 4 else ""


def build_sku(manufacturer: str, oem: str) -> str:
    slug = re.sub(r"[^A-Z0-9]", "-", manufacturer.upper())
    oem_clean = re.sub(r"[^A-Z0-9]", "-", oem.upper())
    return f"{slug}-{oem_clean}"


def stable_uuid(manufacturer: str) -> str:
    h = hashlib.md5(manufacturer.lower().encode()).hexdigest()
    return str(uuid.UUID(h))


# ── Seed data collected from fridayparts.com ─────────────────────────────────

ISUZU_PARTS = [
    # --- TFR ---
    {"part_number": "8-97071673-0", "name": "Push Rod", "price_usd": 42.58, "category": "Engine Parts", "fits_model": "TFR, NHR, NKR, NPR"},
    {"part_number": "8-94214962-0", "name": "Thermostat 82C", "price_usd": 16.99, "category": "Cooling System", "fits_model": "TFR, NKR55, NHR"},
    {"part_number": "5-82550-029-0", "name": "Start Relay", "price_usd": 14.50, "category": "Electrical", "fits_model": "TFR"},
    {"part_number": "24662-22032", "name": "Fuel Filter Assembly", "price_usd": 63.60, "category": "Fuel System", "fits_model": "TFR, UCR55, 600P"},
    {"part_number": "8942481610", "name": "24V Glow Plug Relay", "price_usd": 14.50, "category": "Electrical", "fits_model": "TFR"},
    {"part_number": "8-98145455-0", "name": "SCV Suction Control Valve", "price_usd": 79.99, "category": "Fuel System", "fits_model": "TFR"},
    {"part_number": "8-94175158-0", "name": "12V Glow Plug", "price_usd": 26.50, "category": "Electrical", "fits_model": "TFR, NHR, NKR"},
    {"part_number": "8980042921", "name": "Water Pump", "price_usd": 99.99, "category": "Cooling System", "fits_model": "NPR, NQR, NHR, NKR, TFR"},
    {"part_number": "8-97349416-0", "name": "Cover Gasket", "price_usd": 15.00, "category": "Gaskets", "fits_model": "TFR, NKR55, NKR77"},
    {"part_number": "8-94367292-3", "name": "Fuel Filter", "price_usd": 125.99, "category": "Fuel System", "fits_model": "TFR, UCR55"},
    {"part_number": "8-98196415-0", "name": "Transfer Case Control Motor Actuator", "price_usd": 158.00, "category": "Transmission", "fits_model": "D-MAX, TFR"},
    {"part_number": "8943197002", "name": "12V Glow Plug Set", "price_usd": 42.50, "category": "Electrical", "fits_model": "TFR, NHR, NKR"},
    {"part_number": "8-98145453-1", "name": "Suction Control Valve", "price_usd": 57.00, "category": "Fuel System", "fits_model": "TFR, 4JJ1"},
    {"part_number": "8941618410", "name": "V-Belt", "price_usd": 14.90, "category": "Belts", "fits_model": "TFR, TFS, NHR, NKR"},
    {"part_number": "8973815555", "name": "HP3 Fuel Injection Pump", "price_usd": 450.29, "category": "Fuel System", "fits_model": "TFR, TFS, NPR, NQR, NHR, NKR"},
    # --- NPR ---
    {"part_number": "904-862", "name": "Vacuum Pump with Pulley", "price_usd": 137.67, "category": "Engine Parts", "fits_model": "NPR, NPR-HD"},
    {"part_number": "8-98290-755-0", "name": "4Pcs Glow Plug", "price_usd": 64.89, "category": "Engine Parts", "fits_model": "NPR, NQR, NRR, FTR, FVR"},
    {"part_number": "8980352964", "name": "EGR Valve", "price_usd": 236.99, "category": "Engine Parts", "fits_model": "NPR, NPR-HD, NQR, NRR"},
    {"part_number": "8-98043-686-0", "name": "SCV Suction Control Valve 4HK1", "price_usd": 62.92, "category": "Fuel System", "fits_model": "NPR, NPR-HD, NQR, NRR"},
    {"part_number": "8-97188042-0", "name": "Fuel Water Separator", "price_usd": 33.33, "category": "Fuel System", "fits_model": "NPR-HD, NQR"},
    {"part_number": "8973640780", "name": "Switch Turn Signal Wiper Control", "price_usd": 55.99, "category": "Electrical", "fits_model": "NPR, NQR, NRR, NPR-HD"},
    {"part_number": "8980061870", "name": "RH Front ABS Wheel Speed Sensor", "price_usd": 49.99, "category": "Sensors", "fits_model": "NPR, NQR, NNR"},
    {"part_number": "GF950190N", "name": "Turbo Actuator 4HK1", "price_usd": 176.63, "category": "Turbo System", "fits_model": "NPR, NQR, NRR"},
    {"part_number": "9440610320", "name": "Fuel Feed Pump 4HG1 4HE1", "price_usd": 61.51, "category": "Fuel System", "fits_model": "NPR, NQR, NPR-HD, NKR"},
    {"part_number": "8980584240", "name": "Right Power Window Motor Regulator", "price_usd": 38.99, "category": "Electrical", "fits_model": "NPR-HD, NQR"},
    {"part_number": "8970622940", "name": "Air Filter 4HK1", "price_usd": 36.00, "category": "Filters", "fits_model": "NPR, NQR, NLR, NMR, NPS, NNR"},
    {"part_number": "8981479061", "name": "Turbo RHF55V Turbocharger", "price_usd": 569.99, "category": "Turbo System", "fits_model": "NPR-HD, NPR-XD, NQR, NRR"},
    {"part_number": "8-98246506-3", "name": "NOx Sensor", "price_usd": 124.99, "category": "Sensors", "fits_model": "NPR, NQR, NRR"},
    {"part_number": "8973007902", "name": "Full Set Thermostat 4HE1 4HF1 4HK1", "price_usd": 19.20, "category": "Cooling System", "fits_model": "NPR, NPR-HD, NQR, NRR"},
    {"part_number": "94427224", "name": "Load Sensing Valve Assembly", "price_usd": 165.99, "category": "Brakes", "fits_model": "NPR, NQR, NPS, NRR"},
    {"part_number": "8941311300", "name": "Fuel Hand Priming Feed Pump", "price_usd": 9.58, "category": "Fuel System", "fits_model": "NPR, NQR, NHR, NKR"},
    {"part_number": "8-98346-975-0", "name": "Turbocharger Repair Kit 4HK1", "price_usd": 109.99, "category": "Turbo System", "fits_model": "NPR-HD, NQR, NRR"},
    {"part_number": "29006N6520", "name": "24V Electrical Actuator 4HK1", "price_usd": 174.77, "category": "Turbo System", "fits_model": "NPR, NQR, NRR"},
    {"part_number": "8980277725", "name": "Turbo RHF55V Electronic Actuator", "price_usd": 174.78, "category": "Turbo System", "fits_model": "NPR, NQR"},
    # --- NKR ---
    {"part_number": "5876100881", "name": "Water Pump Assembly 4JH1", "price_usd": 89.31, "category": "Cooling System", "fits_model": "NKR, NHR, NLR, NMR, QKR"},
    {"part_number": "8-97202476-1", "name": "Hazard Switch", "price_usd": 22.00, "category": "Electrical", "fits_model": "NKR, NPR, NHR, ELF"},
    {"part_number": "8-97173951-0", "name": "12V Relay Starter", "price_usd": 45.96, "category": "Electrical", "fits_model": "NKR, NPR, NQR"},
    {"part_number": "8913239352", "name": "24V 11T Starter Motor", "price_usd": 178.09, "category": "Electrical", "fits_model": "NKR, NHR, NHS, NJR, NLR, NPR"},
    {"part_number": "8-97378147-0", "name": "3 Pieces Camshaft Bushing 4JG1 4JG2", "price_usd": 46.32, "category": "Engine Parts", "fits_model": "NKR, NHR, NLR, TFR"},
    {"part_number": "8-94247937-0", "name": "4 Fuel Injectors 4JA1 4JB1", "price_usd": 74.10, "category": "Fuel System", "fits_model": "NKR, NHR"},
    {"part_number": "5-87812-320-0", "name": "Cylinder Head Gasket 4JB1", "price_usd": 36.00, "category": "Gaskets", "fits_model": "NKR"},
    {"part_number": "8970801940", "name": "Oil Pan Gasket 4JA1 4JB1", "price_usd": 34.68, "category": "Gaskets", "fits_model": "NKR, NHR, NKR55"},
    {"part_number": "5-12111-622-2", "name": "Standard Piston Kit with Ring 4JB1", "price_usd": 209.00, "category": "Engine Parts", "fits_model": "NKR"},
    {"part_number": "104642-1651", "name": "VE4 Fuel Injection Pump 4HF1", "price_usd": 710.99, "category": "Fuel System", "fits_model": "NKR, NPR, NQR"},
    # --- DMAX ---
    {"part_number": "8980118881", "name": "Common Rail Assembly 4JJ1", "price_usd": 420.89, "category": "Fuel System", "fits_model": "D-MAX, NKR, NQR"},
    {"part_number": "8982043270", "name": "Turbo RHF4 Turbocharger 4JJ1", "price_usd": 202.67, "category": "Turbo System", "fits_model": "D-MAX"},
    {"part_number": "8-97355980-0", "name": "Power Steering Oil Pump Assembly", "price_usd": 189.00, "category": "Steering", "fits_model": "D-MAX"},
    {"part_number": "8976629010", "name": "Crankshaft 4JJ1", "price_usd": 2350.59, "category": "Engine Parts", "fits_model": "D-MAX, NLR, NMR"},
    {"part_number": "8980839230", "name": "CR14 Air Conditioning Compressor", "price_usd": 148.80, "category": "HVAC", "fits_model": "D-MAX, TFR, TFS"},
    {"part_number": "8-97946697-0", "name": "Power Steering Oil Pump 4JJ1 4JK1", "price_usd": 169.00, "category": "Steering", "fits_model": "D-MAX"},
    {"part_number": "8-97105872-1", "name": "Cylinder Head Gasket 4JJ1", "price_usd": 49.98, "category": "Gaskets", "fits_model": "D-MAX, NKR77, NPR"},
    {"part_number": "8981650710", "name": "Oil Filter 4JJ1", "price_usd": 20.50, "category": "Filters", "fits_model": "D-MAX"},
    {"part_number": "8-98171310-0", "name": "Radiator D-MAX 2003-2006", "price_usd": 609.52, "category": "Cooling System", "fits_model": "D-MAX, TFR"},
    {"part_number": "8-97942296-0", "name": "Clutch Slave Cylinder 4JA1", "price_usd": 53.80, "category": "Clutch", "fits_model": "D-MAX"},
    {"part_number": "295700-1060", "name": "Fuel Injector 4JJ1", "price_usd": 279.45, "category": "Fuel System", "fits_model": "D-MAX"},
    # --- ELF ---
    {"part_number": "8-94368249-2", "name": "Fuel Return Pipe 4JG2", "price_usd": 19.25, "category": "Fuel System", "fits_model": "ELF, D-MAX"},
    {"part_number": "8-98009418-0", "name": "Map Sensor 4HK1", "price_usd": 19.68, "category": "Sensors", "fits_model": "ELF, NQR, NPR, D-MAX, 700P"},
    {"part_number": "8976069430", "name": "Crankshaft Position Sensor 4HK1", "price_usd": 36.93, "category": "Sensors", "fits_model": "ELF, FRR, FSR, FTR, FVR"},
    {"part_number": "8973718331", "name": "Fuel Injection Pipe 4HK1", "price_usd": 67.05, "category": "Fuel System", "fits_model": "ELF, NPR, NQR, 700P"},
    {"part_number": "1443801530", "name": "Drag Link", "price_usd": 198.60, "category": "Steering", "fits_model": "ELF, NPR, NHR, NMR"},
    {"part_number": "8-98032603-0", "name": "Brake Master Cylinder 4HK1", "price_usd": 297.00, "category": "Brakes", "fits_model": "ELF, NPR, NQR, 700P"},
    {"part_number": "8970266734", "name": "Fuel Injection Pump D201", "price_usd": 849.99, "category": "Fuel System", "fits_model": "ELF"},
    {"part_number": "5878153850", "name": "Overhaul Gasket Kit 4JJ1 TIER 3", "price_usd": 98.59, "category": "Gaskets", "fits_model": "ELF, NPR, NQR, NHR, NKR"},
    {"part_number": "8980206490", "name": "6Pcs Piston Cooling Oil Jet", "price_usd": 110.00, "category": "Engine Parts", "fits_model": "ELF, FRR, FSR, NQR, NPR"},
    # --- FRR ---
    {"part_number": "8980325490", "name": "Fuel Pressure Relief Limiter Valve", "price_usd": 43.99, "category": "Fuel System", "fits_model": "FRR, FTR, FVR, NPR, NQR"},
    {"part_number": "8943906401", "name": "Turbo GT3576 Turbocharger 6HK1", "price_usd": 377.55, "category": "Turbo System", "fits_model": "FRR, FSR, FTR, FTS, FVR"},
    {"part_number": "1-79138199-4", "name": "Car Lock Cylinder Set", "price_usd": 66.50, "category": "Body Parts", "fits_model": "FRR, FTR, FVR, CYZ"},
    {"part_number": "8-97386557-5", "name": "Fuel Injection Pump 4HK1 4LE2", "price_usd": 599.00, "category": "Fuel System", "fits_model": "FRR, FSR, FSS, NNR, NPR"},
    {"part_number": "8-98328207-0", "name": "Oil Filter 4JJ1 4HK1", "price_usd": 63.90, "category": "Filters", "fits_model": "FRR, FSR, 700P"},
    {"part_number": "8980642820", "name": "1 Set Connecting Rod Bearing 4HK1", "price_usd": 65.00, "category": "Engine Parts", "fits_model": "FRR, FSR, FTR"},
    {"part_number": "8-97077638-0", "name": "1 Set Cylinder Head Bolt 6HK1", "price_usd": 171.59, "category": "Engine Parts", "fits_model": "FRR, FSR, FTR, FVZ, GVR"},
    {"part_number": "8-98019024-0", "name": "Camshaft Position Sensor 4HK1 6HK1", "price_usd": 19.20, "category": "Sensors", "fits_model": "FRR, FSR, FTR, FVR, FVZ, ELF"},
    {"part_number": "8-97600586-1", "name": "Idler Gear 4HK1 6HK1", "price_usd": 289.00, "category": "Engine Parts", "fits_model": "FRR, FSR, FTR, FVZ"},
    {"part_number": "8-97386189-0", "name": "1 Set Camshaft Bearing 4HK1", "price_usd": 52.00, "category": "Engine Parts", "fits_model": "FRR, FSR, FTR, NKR, NQR"},
    {"part_number": "8-94391602-1", "name": "Cylinder Liner 4HK1", "price_usd": 112.60, "category": "Engine Parts", "fits_model": "FRR, NNR, NPR, NPS, NQR"},
    # --- FTR ---
    {"part_number": "1157610061", "name": "Fuel Hand Primer Pump", "price_usd": 9.40, "category": "Fuel System", "fits_model": "FTR, FRR, NPR, NQR"},
    {"part_number": "38780", "name": "Wheel Hub Seal", "price_usd": 37.20, "category": "Wheels", "fits_model": "FTR"},
    {"part_number": "8-97601156-1", "name": "Fuel Injector 4HK1 6HK1", "price_usd": 133.30, "category": "Fuel System", "fits_model": "FTR, FVR"},
    {"part_number": "8-98275-909-0", "name": "Exhaust Particulate Matter PM Sensor", "price_usd": 139.00, "category": "Sensors", "fits_model": "FTR, NPR, NQR, NRR"},
    {"part_number": "5WK97210", "name": "Nitrogen Oxide Nox Sensor", "price_usd": 105.99, "category": "Sensors", "fits_model": "FTR, NPR, NQR, NRR"},
    {"part_number": "1831610070", "name": "Water Temperature Sensor", "price_usd": 18.50, "category": "Cooling System", "fits_model": "FTR, FVR, NRR"},
    {"part_number": "8-98147-525-0", "name": "Fuel Filter 4HK1", "price_usd": 34.99, "category": "Fuel System", "fits_model": "FTR, FVR, NPR-HD, NQR, NRR"},
    {"part_number": "1137700700", "name": "Thermostat 6BD1 6BG1", "price_usd": 25.00, "category": "Cooling System", "fits_model": "FTR, FSR, NRR"},
    {"part_number": "8981095701", "name": "10S13C A/C Compressor", "price_usd": 248.90, "category": "HVAC", "fits_model": "FTR, NPR, NQR, NRR"},
    {"part_number": "8-97602803-4", "name": "6 Pieces Fuel Injector 6HK1", "price_usd": 562.12, "category": "Fuel System", "fits_model": "FTR, FVR"},
    # --- 700P ---
    {"part_number": "8-97310496-1", "name": "Exhaust Gas Recirculation Cooler Assembly", "price_usd": 223.71, "category": "Engine Parts", "fits_model": "700P, NPR"},
    {"part_number": "898027-7731", "name": "Turbo RHF55V Turbocharger 4HK1", "price_usd": 449.42, "category": "Turbo System", "fits_model": "700P, NQR, NPR"},
    {"part_number": "8980504152", "name": "Air Cleaner Assembly", "price_usd": 235.80, "category": "Filters", "fits_model": "700P, NPR75, NQR75"},
    {"part_number": "8972550690", "name": "24V Fuel Shut Off Solenoid", "price_usd": 77.90, "category": "Fuel System", "fits_model": "700P"},
    {"part_number": "8-98037101-2", "name": "Front Door Outside Handle", "price_usd": 64.00, "category": "Body Parts", "fits_model": "700P, NQR, NPR"},
    {"part_number": "8-97261550-0", "name": "Front Axle Knuckle", "price_usd": 179.00, "category": "Suspension", "fits_model": "700P"},
    {"part_number": "8-97367381-0", "name": "Cooling Fan 4HK1", "price_usd": 91.50, "category": "Engine Parts", "fits_model": "700P, NPR"},
    {"part_number": "8-98023883-0", "name": "Water Temperature Sensor 4HK1", "price_usd": 32.99, "category": "Sensors", "fits_model": "700P, NPR, NQR"},
    {"part_number": "8-98110220-5", "name": "Steering Unit", "price_usd": 664.38, "category": "Steering", "fits_model": "700P"},
    {"part_number": "8971801991", "name": "Cooling Fan Belt", "price_usd": 24.99, "category": "Engine Parts", "fits_model": "700P, NPR, ELF"},
    {"part_number": "8-97386922-0", "name": "Hazard Switch 700P", "price_usd": 17.50, "category": "Electrical", "fits_model": "700P, VC46"},
    {"part_number": "8982597790", "name": "24V 5 Pins Starter Relay", "price_usd": 44.79, "category": "Electrical", "fits_model": "700P, FRR, FSR, FTR, FVR, NPR75"},
    {"part_number": "8100010-P301", "name": "A/C Compressor 700P", "price_usd": 310.99, "category": "HVAC", "fits_model": "700P"},
    # --- NHR extra ---
    {"part_number": "8-94462-137-1", "name": "Oil Filter Assembly 4JG2 C240", "price_usd": 157.00, "category": "Filters", "fits_model": "NHR, NKR"},
    # --- NQR ---
    {"part_number": "8-97359985-2", "name": "Differential Pressure Sensor", "price_usd": 41.99, "category": "Sensors", "fits_model": "NQR, NPR, CYZ, FRR"},
    {"part_number": "8-97173951-0", "name": "12V Relay Starter 4JJ1 4HK1", "price_usd": 45.96, "category": "Electrical", "fits_model": "NQR, NPR, NRR"},
    # --- FRR extra ---
    {"part_number": "8-97306040-0", "name": "EGR Valve Gasket 4HK1", "price_usd": 17.99, "category": "Gaskets", "fits_model": "FRR, FSR, FTR, FVR, NPR, NRR"},
    {"part_number": "1123104700", "name": "Crankshaft 6BG1", "price_usd": 876.99, "category": "Engine Parts", "fits_model": "FRR, FSR, FTR"},
    {"part_number": "8973886440", "name": "Exhaust Valve 6UZ1", "price_usd": 45.52, "category": "Engine Parts", "fits_model": "FRR, FSR"},
    {"part_number": "8-97173951-0", "name": "12V Starter Relay", "price_usd": 45.96, "category": "Electrical", "fits_model": "NPR, NQR, NRR, FRR"},
    # --- FRR/FTR/700P unique ---
    {"part_number": "8980206490", "name": "Piston Cooling Oil Jet 4HK1 6HK1", "price_usd": 110.00, "category": "Engine Parts", "fits_model": "FRR, FSR, NQR75"},
    {"part_number": "8976069430", "name": "Crankshaft Position Sensor 6HK1", "price_usd": 36.93, "category": "Sensors", "fits_model": "FRR, FSR, FTR, ELF"},
    {"part_number": "8-98019024-0", "name": "Camshaft Position Sensor 6HK1", "price_usd": 19.20, "category": "Sensors", "fits_model": "FRR, FSR, FTR, FVR"},
    {"part_number": "8-97600586-1", "name": "Idler Gear FRR", "price_usd": 289.00, "category": "Engine Parts", "fits_model": "FRR, FSR"},
    {"part_number": "8973007872", "name": "Thermostat Set 4HF1", "price_usd": 19.20, "category": "Cooling System", "fits_model": "NPR, NQR, NRR"},
    {"part_number": "8980584290A", "name": "Left Power Window Motor Regulator", "price_usd": 38.99, "category": "Electrical", "fits_model": "NPR-HD, NQR"},
    # --- DMAX extra ---
    {"part_number": "8-97602803-4", "name": "6 Pieces Fuel Injector 6HK1 T6500", "price_usd": 562.12, "category": "Fuel System", "fits_model": "FTR, FVR, T6500"},
]

# Note: Daihatsu parts from fridayparts.com are industrial engines (DM950/DM850), not car parts.
# Skipping for this seed — Daihatsu cars need a different source.

DAIHATSU_PARTS: list = []

ALL_PARTS = [
    ("Isuzu", ISUZU_PARTS),
]


async def import_brand(conn: asyncpg.Connection, manufacturer: str, raw_parts: list) -> dict:
    mfr_id = BRAND_IDS.get(manufacturer) or stable_uuid(manufacturer)
    seen_oem: set = set()
    inserted = 0
    skipped = 0

    for raw in raw_parts:
        oem = clean_oem(raw.get("part_number", ""))
        if not oem or oem in seen_oem:
            skipped += 1
            continue
        seen_oem.add(oem)

        sku = build_sku(manufacturer, oem)
        name = (raw.get("name") or oem).strip()[:255]
        price_usd = float(raw.get("price_usd") or 0)
        price_ils = round(price_usd * USD_TO_ILS, 2)
        category = map_cat(raw.get("category") or "")
        desc = f"{name}. Fits: {raw.get('fits_model', '')}."

        try:
            async with conn.transaction():
                part_id = await conn.fetchval("""
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, manufacturer, manufacturer_id,
                        category, description, specifications,
                        online_price_ils, min_price_ils, max_price_ils,
                        part_type, is_safety_critical, needs_oem_lookup,
                        master_enriched, is_active, created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), $1, $2, $3, $4, $5::uuid,
                        $6, $7, '{}'::jsonb,
                        $8, $8, $9,
                        'original', FALSE, FALSE,
                        FALSE, TRUE, NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        name = EXCLUDED.name,
                        online_price_ils = EXCLUDED.online_price_ils,
                        min_price_ils = EXCLUDED.min_price_ils,
                        updated_at = NOW()
                    RETURNING id
                """, sku, oem, name, manufacturer, mfr_id,
                     category, desc, price_ils, round(price_ils * 1.18, 2))
                if part_id:
                    inserted += 1
        except Exception as e:
            log.warning("Failed to insert %s: %s", sku, e)
            skipped += 1

    return {"manufacturer": manufacturer, "inserted": inserted, "skipped": skipped, "total": len(raw_parts)}


async def main() -> None:
    conn = await asyncpg.connect(DB_DSN)
    try:
        for manufacturer, parts in ALL_PARTS:
            log.info("Importing %s (%d parts)...", manufacturer, len(parts))
            result = await import_brand(conn, manufacturer, parts)
            log.info("  %s: inserted=%d skipped=%d", manufacturer, result["inserted"], result["skipped"])

        # Verify
        for manufacturer, _ in ALL_PARTS:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM parts_catalog WHERE manufacturer=$1 AND is_active=TRUE",
                manufacturer
            )
            log.info("DB count for %s: %d active parts", manufacturer, count)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
