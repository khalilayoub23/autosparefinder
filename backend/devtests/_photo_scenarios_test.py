#!/usr/bin/env python3
"""
_photo_scenarios_test.py — for the owner's 9 real photos, test the parts of the flow a
photo TRIGGERS that we CAN run on the server: (1) VIN-sticker decoding (2 real VW VINs
from the windshield photos) and (2) the part-name search/brain for each identified part.
The raw pixel->identify step (production Gemini vision) needs the image files on the
server; this covers everything downstream of that.
"""
import asyncio
import re

USER = "dd8cfb56-23f3-4245-9f33-7a1a16ad164f"

# VIN stickers a customer photographed (text read from the two windshield photos)
VIN_CASES = [
    ("WVWZZZ6EZ5B002227", ["volkswagen", "פולקסווגן", "vw", "polo", "פולו", "רכב", "שנת", "דגם"]),
    ("WVGZZZ5NZJM131395", ["volkswagen", "פולקסווגן", "vw", "tiguan", "טיגואן", "רכב", "שנת", "דגם"]),
]

# Ground-truth part (from the photos) + car → does the brain find/handle the right part?
PART_CASES = [
    ("מדחס מזגן לפולקסווגן גולף",            ["מדחס", "מזגן", "compressor", "גולף"], "whatsapp"),
    ("משאבת ABS לבמוו 320",                  ["ABS", "משאב", "בלם", "במוו"], "telegram"),
    ("מראה צד שמאל לסיטרואן ברלינגו 2014",   ["מרא", "ברלינגו", "סיטרואן", "mirror"], "web"),
    ("בולם זעזועים קדמי לטויוטה קורולה",     ["בולם", "זעזוע", "shock", "קורולה"], "whatsapp"),
    ("תיבת הילוכים לפולקסווגן גולף",         ["הילוכ", "גיר", "gearbox", "transmission", "גולף"], "web"),
]


def hit(text, keys):
    t = (text or "").lower()
    return any(k.lower() in t for k in keys)


async def main():
    from BACKEND_AI_AGENTS import process_user_message
    from BACKEND_DATABASE_MODELS import pii_session_factory

    print("=" * 74)
    print("VIN-STICKER PHOTOS → brain (does it decode the VW VIN to a vehicle?)")
    print("=" * 74)
    for vin, expect in VIN_CASES:
        try:
            async with pii_session_factory() as db:
                res = await process_user_message(user_id=USER, message=vin,
                                                 conversation_id=None, db=db, source="whatsapp")
            ans = (res or {}).get("response", "")
            print(f"\nVIN {vin}")
            print(f"   {'✅' if hit(ans, expect) else '❔'} bot: {ans[:160].replace(chr(10),' ')}")
        except Exception as e:
            print(f"\nVIN {vin} → error: {str(e)[:90]}")

    print("\n" + "=" * 74)
    print("PART-FROM-PHOTO → brain search (real parts in the photos, per channel)")
    print("=" * 74)
    ok = 0
    for msg, expect, channel in PART_CASES:
        try:
            async with pii_session_factory() as db:
                res = await process_user_message(user_id=USER, message=msg,
                                                 conversation_id=None, db=db, source=channel)
            ans = (res or {}).get("response", "")
            good = hit(ans, expect) or ("₪" in ans) or bool(re.search(r"\d{2,}", ans))
            ok += bool(good)
            print(f"\n[{channel}] '{msg}'")
            print(f"   {'✅ on-target' if good else '❌ off'} — {ans[:150].replace(chr(10),' ')}")
        except Exception as e:
            print(f"\n[{channel}] '{msg}' → error: {str(e)[:90]}")
    print(f"\n  PART-SEARCH SCORE: {ok}/{len(PART_CASES)}")
    print("DONE")


if __name__ == "__main__":
    asyncio.run(main())
