"""Ad-hoc conversation tester — drives multi-turn chats through the live agent
brain (process_user_message) as if a real customer, prints each exchange.
Run: docker exec autospare_backend python3 /app/devtests/_chat_test.py <scenario>
"""
import asyncio, sys, uuid
sys.path.insert(0, "/app")

async def run_convo(title, turns, source="web"):
    from BACKEND_AI_AGENTS import process_user_message
    from BACKEND_DATABASE_MODELS import pii_session_factory
    # Multi-turn persistence: the FIRST call creates the conversation and returns
    # its real id; every later turn must reuse THAT id (a random uuid that does
    # not exist yet makes process_user_message spawn a throwaway conversation per
    # turn — exactly how the webhooks look up the existing conversation by
    # chat_id and pass its real id).
    conv_id = None
    anon = "00000000-0000-0000-0000-000000000001"
    print(f"\n{'='*70}\n  SCENARIO: {title}   (source={source})\n{'='*70}")
    for msg in turns:
        print(f"\n🧑 CUSTOMER: {msg}")
        async with pii_session_factory() as db:
            try:
                r = await process_user_message(user_id=anon, message=msg,
                                                conversation_id=conv_id, db=db, source=source)
                if isinstance(r, dict):
                    reply = r.get("response") or r.get("reply") or r.get("message") or str(r)
                    conv_id = r.get("conversation_id") or conv_id
                else:
                    reply = str(r)
            except Exception as e:
                reply = f"[ERROR: {e}]"
        print(f"🤖 AGENT: {reply[:700]}")
        await asyncio.sleep(0.5)

SCENARIOS = {
  "ask_buy": ("Asking → Buying full cycle", [
      "היי", "אני מחפש מסנן שמן לטויוטה קורולה 2018", "כמה זה עולה?",
      "כן אני רוצה להזמין", "יש משלוח לבאר שבע?",
  ]),
  "shipping_finance": ("Shipping + financial details", [
      "אני צריך רפידות בלם קדמיות למאזדה 3 2016",
      "כמה זמן לוקח המשלוח ולכמה זה יוצא כולל הכל?",
      "אפשר לשלם באשראי? ומה זה כולל מעמ?",
  ]),
  "promo_nuance": ("Promotions + human nuance", [
      "יש לכם הנחות או קופונים?",
      "אחי אני קונה הרבה, תן לי הנחה טובה",
      "טוב מעצבן, סתם שאלתי", "רגע לא התכוונתי, אתה בסדר. יש חיישן חמצן להונדה סיוויק?",
  ]),
}

if __name__ == "__main__":
    key = sys.argv[1] if len(sys.argv) > 1 else "ask_buy"
    title, turns = SCENARIOS[key]
    asyncio.run(run_convo(title, turns))
