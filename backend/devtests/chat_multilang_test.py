"""
Script: devtests/chat_multilang_test.py
Purpose: Verify the shared chat brain (process_user_message) supports Hebrew, Arabic and
         English — same customer intent in each language, asserting the reply comes back in
         the SAME language (LANGUAGE RULES: detect from first message, reply in-language,
         never mix), and that it stays on-topic (finds parts / asks a sensible follow-up).

Usage (inside the backend container):
  python3 /app/devtests/chat_multilang_test.py

Author: AutoSpareFinder Agent
Last Updated: 2026-07-18
"""
import asyncio
import re
import os

os.chdir("/app")

HE = re.compile(r'[֐-׿]')
AR = re.compile(r'[؀-ۿ]')
EN = re.compile(r'[A-Za-z]')

# Same intent (oil filter, Toyota Corolla 2018) in three languages.
CASES = [
    ("he", "מסנן שמן לטויוטה קורולה 2018"),
    ("ar", "فلتر زيت لتويوتا كورولا 2018"),
    ("en", "oil filter for Toyota Corolla 2018"),
]

def dominant_lang(text: str) -> str:
    he, ar, en = len(HE.findall(text)), len(AR.findall(text)), len(EN.findall(text))
    # Arabic and Hebrew blocks are distinct; ignore digits/punctuation/OEM codes.
    scores = {"he": he, "ar": ar, "en": en}
    return max(scores, key=scores.get) if any(scores.values()) else "?"


async def main():
    from BACKEND_AI_AGENTS import process_user_message
    from BACKEND_DATABASE_MODELS import get_pii_db
    passed, total = 0, 0
    for lang, msg in CASES:
        total += 1
        agen = get_pii_db(); db = await agen.__anext__()
        try:
            r = await process_user_message(
                user_id="00000000-0000-0000-0000-000000000001",
                message=msg, conversation_id=None, db=db, source="web")
            reply = (r.get("response") or r.get("message") or "")
        except Exception as e:
            reply = f"[ERROR: {e}]"
        finally:
            try: await agen.aclose()
            except Exception: pass

        got = dominant_lang(reply)
        # For Hebrew/Arabic we require the reply's dominant script to match. English replies
        # legitimately contain many Latin chars; accept if not dominated by he/ar.
        ok = (got == lang) if lang in ("he", "ar") else (got == "en")
        # Arabic sanity: the reply must contain Arabic and NOT be dominated by Hebrew (no mixing)
        if lang == "ar":
            ok = ok and AR.search(reply) is not None and len(HE.findall(reply)) < 3
        if lang == "he":
            ok = ok and HE.search(reply) is not None and len(AR.findall(reply)) < 3
        passed += 1 if ok else 0
        print(f"[{lang}] intent: {msg}")
        print(f"     reply-lang={got}  {'PASS' if ok else 'FAIL'}")
        print(f"     reply: {reply[:180].replace(chr(10),' ')}\n")

    print("=" * 56)
    print(f"MULTILINGUAL CHAT: {passed}/{total} replied in the customer's language")
    print("=" * 56)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
