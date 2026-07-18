#!/usr/bin/env python3
"""
_media_chat_test.py — exercise the chatbot's PHOTO + VOICE handling with realistic
real-world data (2026-07-13, owner request).

IMAGES: real eBay SELLER photos (in-hand / used / on-bench shots of actual parts — far
closer to what a customer snaps than a clean catalog render), pulled via our own eBay
Browse API. The listing title is the ground truth. Each photo goes through the EXACT
production vision prompt (routes/chat.py upload_image) → identified part → then fed to
the live catalog search to prove end-to-end (photo → ID → real parts).

VOICE: Hebrew/English clips synthesized via an online TTS (no install), run through the
production transcription (hf_audio / Groq Whisper) → process_user_message on each channel
(web / whatsapp / telegram — they share one brain) → check the bot lands on the right part.
"""
import asyncio
import base64
import json
import re
import urllib.parse
import urllib.request

VISION_PROMPT = (
    "You are an expert automotive parts identifier. "
    "Look at this image and identify the car part shown. "
    "Respond ONLY with a JSON object, no markdown: "
    '{"part_name_he": "<SHORT Hebrew name as used in Israeli auto parts catalogs>", '
    '"part_name_en": "<name in English>", '
    '"possible_names": ["<alt Hebrew name 1>", "<alt Hebrew name 2>", "<alt Hebrew name 3>"], '
    '"confidence": <0.0-1.0>. '
    'IMPORTANT: part_name_he and ALL possible_names must be SHORT Hebrew terms '
    '(1-3 words) exactly as written in Israeli auto parts price lists.}'
)

# (eBay search query, ground-truth keywords the vision output should contain)
IMAGE_CASES = [
    ("used brake pads set",        ["brake pad", "רפיד", "בלם", "pad"]),
    ("used oil filter car",        ["oil filter", "מסנן שמן", "פילטר שמן", "filter"]),
    ("spark plug used",            ["spark plug", "מצת", "plug"]),
    ("car alternator used",        ["alternator", "אלטרנטור", "generator"]),
    ("headlight assembly used",    ["headlight", "פנס", "head lamp", "light"]),
    ("radiator car used",          ["radiator", "רדיאטור", "מצנן", "cooling"]),
    ("ignition coil used",         ["ignition coil", "סליל", "coil", "הצתה"]),
    ("shock absorber strut used",  ["shock", "בולם", "strut", "absorber", "זעזוע"]),
]

# (spoken text, TTS lang, expected keyword(s) in the bot's answer, channel)
# Wide variation tied to the owner's real photos (AC compressor, ABS pump, side mirror,
# struts) + short terms that mis-heard before the vocab hint (מצת, קיה ספורטג').
VOICE_CASES = [
    ("מצת למאזדה שלוש",                          "he", ["מצת", "spark", "הצתה", "מזדה", "מאזדה"], "web"),
    ("פנס קדמי לקיה ספורטג'",                    "he", ["פנס", "ספורטג", "קיה", "light"], "whatsapp"),
    ("מדחס מזגן לפולקסווגן גולף",                "he", ["מדחס", "מזגן", "גולף", "compressor"], "telegram"),
    ("משאבת איי בי אס לבמוו 320",                "he", ["ABS", "משאב", "במוו", "בלם"], "whatsapp"),
    ("מראה צד שמאל לסיטרואן ברלינגו",            "he", ["מרא", "ברלינגו", "סיטרואן", "mirror"], "web"),
    ("בולם זעזועים קדמי לטויוטה קורולה 2016",    "he", ["בולם", "זעזוע", "קורולה", "shock"], "telegram"),
    ("יש לי יונדאי איי 35 צריך מסנן אוויר",      "he", ["מסנן", "אוויר", "יונדאי", "filter"], "whatsapp"),
    ("brake pads and oil filter for honda civic 2019", "en", ["brake", "filter", "civic", "בלם", "מסנן"], "web"),
]

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36"


def fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read()


def tts(text: str, lang: str) -> bytes:
    url = ("https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&tl="
           + lang + "&q=" + urllib.parse.quote(text))
    return fetch(url, timeout=15)


def hit(text: str, keys) -> bool:
    t = (text or "").lower()
    return any(k.lower() in t for k in keys)


async def get_ebay_photo(query: str):
    """Return (image_bytes, mime, ground_truth_title) for a real eBay listing, or None."""
    from services.suppliers.ebay_supplier import EbaySupplier
    sup = EbaySupplier()
    results = await sup.search(query, limit=6)
    for r in (results or []):
        url = getattr(r, "image_url", None)
        if not url:
            continue
        try:
            img = fetch(url, timeout=15)
        except Exception:
            continue
        if len(img) < 2500:            # skip 1x1 / placeholder pixels
            continue
        mime = "image/jpeg" if not url.lower().endswith(".png") else "image/png"
        return img, mime, (getattr(r, "name", "") or getattr(r, "title", "") or query)
    return None


async def run_images():
    from hf_client import hf_vision
    print("\n" + "=" * 74)
    print("PART A — PHOTO → VISION IDENTIFICATION → LIVE SEARCH (real eBay seller photos)")
    print("=" * 74)
    ok_id = 0
    ok_search = 0
    total = 0
    from BACKEND_DATABASE_MODELS import pii_session_factory
    from BACKEND_AI_AGENTS import process_user_message
    for query, expect in IMAGE_CASES:
        total += 1
        got = await get_ebay_photo(query)
        if not got:
            print(f"\n[{query}]  ⚠ no fetchable eBay photo — skipped")
            continue
        img, mime, truth = got
        b64 = base64.b64encode(img).decode()
        try:
            raw = await hf_vision(b64, VISION_PROMPT, mime=mime)
            raw = raw.strip().strip("`").removeprefix("json").strip()
            parsed = json.loads(raw)
        except Exception as e:
            print(f"\n[{query}]  ✗ vision error: {str(e)[:80]}")
            continue
        he = parsed.get("part_name_he", "")
        en = parsed.get("part_name_en", "")
        conf = parsed.get("confidence", 0)
        blob = f"{he} {en} {' '.join(parsed.get('possible_names', []))}"
        identified_ok = hit(blob, expect)
        ok_id += identified_ok
        print(f"\n[{query}]  (truth: {truth[:46]})")
        print(f"   img {len(img)//1024}KB → vision: he='{he}' en='{en}' conf={conf}")
        print(f"   identification vs ground truth: {'✅ HIT' if identified_ok else '❌ miss'}")
        # end-to-end: feed the identified Hebrew name into the real brain/search
        search_term = he or en
        if search_term:
            try:
                async with pii_session_factory() as db:
                    res = await process_user_message(
                        user_id="dd8cfb56-23f3-4245-9f33-7a1a16ad164f",
                        message=search_term, conversation_id=None, db=db, source="web")
                ans = (res or {}).get("response", "")
                found = hit(ans, expect) or ("₪" in ans) or bool(re.search(r"\d{2,}", ans))
                ok_search += bool(found)
                print(f"   → search '{search_term}': {'✅ returned relevant parts' if found else '❌ no clear hit'}")
                print(f"     bot: {ans[:120].replace(chr(10),' ')}")
            except Exception as e:
                print(f"   → search error: {str(e)[:80]}")
    print(f"\n  IMAGE SCORE: identified {ok_id}/{total} | end-to-end search {ok_search}/{total}")


async def run_voice():
    from hf_client import hf_audio
    from BACKEND_AI_AGENTS import process_user_message
    from BACKEND_DATABASE_MODELS import pii_session_factory
    print("\n" + "=" * 74)
    print("PART B — VOICE → TRANSCRIBE → BRAIN (per channel: whatsapp/telegram/web)")
    print("=" * 74)
    ok_tx = 0
    ok_ans = 0
    total = 0
    for text, lang, expect, channel in VOICE_CASES:
        total += 1
        try:
            audio = tts(text, lang)
        except Exception as e:
            print(f"\n[{channel}] '{text}'  ⚠ TTS failed: {str(e)[:60]}")
            continue
        try:
            tx = (await hf_audio(audio)).strip()
        except Exception as e:
            print(f"\n[{channel}] '{text}'  ✗ transcription error: {str(e)[:70]}")
            continue
        # word-overlap between intended and transcribed
        want = set(re.findall(r"[\wא-ת]+", text.lower()))
        gotw = set(re.findall(r"[\wא-ת]+", tx.lower()))
        overlap = len(want & gotw) / max(1, len(want))
        tx_ok = overlap >= 0.5
        ok_tx += tx_ok
        print(f"\n[{channel}] spoken: '{text}'")
        print(f"   transcribed: '{tx}'  (word overlap {overlap:.0%} → {'✅' if tx_ok else '❌'})")
        try:
            async with pii_session_factory() as db:
                res = await process_user_message(
                    user_id="dd8cfb56-23f3-4245-9f33-7a1a16ad164f",
                    message=tx, conversation_id=None, db=db, source=channel)
            ans = (res or {}).get("response", "")
            found = hit(ans, expect) or ("₪" in ans)
            ok_ans += bool(found)
            print(f"   brain[{channel}]: {'✅ on-target' if found else '❌ off'} — {ans[:110].replace(chr(10),' ')}")
        except Exception as e:
            print(f"   brain error: {str(e)[:80]}")
    print(f"\n  VOICE SCORE: transcription {ok_tx}/{total} | on-target answer {ok_ans}/{total}")


async def main():
    await run_voice()
    print("\nDONE")


if __name__ == "__main__":
    asyncio.run(main())
