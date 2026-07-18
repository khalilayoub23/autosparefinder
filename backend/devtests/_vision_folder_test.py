#!/usr/bin/env python3
"""
_vision_folder_test.py — run the PRODUCTION vision pipeline on every image in
backend/test_images/ (drop real photos there). For each: identify via the exact
upload_image prompt (Gemini 2.0 Flash) → then feed the identified Hebrew name into
the live brain/search. Prints identification + confidence + whether it lands parts.

Run: docker exec autospare_backend python3 /app/devtests/_vision_folder_test.py
"""
import asyncio
import base64
import json
import os
import re

IMG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test_images")
USER = "dd8cfb56-23f3-4245-9f33-7a1a16ad164f"

VISION_PROMPT = (
    "You are an expert automotive parts identifier. "
    "Look at this image and identify the car part shown. "
    "If SEVERAL parts are visible (e.g. mounted in the engine bay), identify the "
    "part in the CENTER/FOREGROUND the photo is framed on — NOT the largest hose/"
    "pipe/duct/cover in the background. If crowded and unsure, lower confidence. "
    "Respond ONLY with a JSON object, no markdown: "
    '{"part_name_he": "<SHORT Hebrew name as used in Israeli auto parts catalogs>", '
    '"part_name_en": "<name in English>", '
    '"possible_names": ["<alt Hebrew 1>", "<alt Hebrew 2>", "<alt Hebrew 3>"], '
    '"confidence": <0.0-1.0>. '
    'If the image is a VIN plate/sticker, set part_name_en to "VIN" and put the code in possible_names. '
    'part_name_he and possible_names must be SHORT Hebrew terms (1-3 words).}'
)

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


async def main():
    from hf_client import hf_vision
    from BACKEND_AI_AGENTS import process_user_message
    from BACKEND_DATABASE_MODELS import pii_session_factory

    files = sorted(f for f in os.listdir(IMG_DIR)
                   if os.path.splitext(f)[1].lower() in MIME) if os.path.isdir(IMG_DIR) else []
    if not files:
        print(f"No images in {IMG_DIR} — drop real .jpg/.png photos there first.")
        return
    print(f"Running production vision on {len(files)} image(s) in {IMG_DIR}\n" + "=" * 70)
    for fn in files:
        path = os.path.join(IMG_DIR, fn)
        with open(path, "rb") as fh:
            b = fh.read()
        b64 = base64.b64encode(b).decode()
        mime = MIME[os.path.splitext(fn)[1].lower()]
        print(f"\n[{fn}]  ({len(b)//1024}KB)")
        try:
            raw = await hf_vision(b64, VISION_PROMPT, mime=mime)
            raw = raw.strip().strip("`").removeprefix("json").strip()
            p = json.loads(raw)
        except Exception as e:
            print(f"   ✗ vision error: {str(e)[:100]}")
            continue
        he, en = p.get("part_name_he", ""), p.get("part_name_en", "")
        print(f"   → identified: he='{he}' en='{en}' conf={p.get('confidence')} "
              f"alts={p.get('possible_names')}")
        term = he or en
        if term and en.upper() != "VIN":
            try:
                async with pii_session_factory() as db:
                    res = await process_user_message(user_id=USER, message=term,
                                                     conversation_id=None, db=db, source="web")
                ans = (res or {}).get("response", "")
                print(f"   → search '{term}': {ans[:130].replace(chr(10),' ')}")
            except Exception as e:
                print(f"   → search error: {str(e)[:80]}")
    print("\nDONE")


if __name__ == "__main__":
    asyncio.run(main())
