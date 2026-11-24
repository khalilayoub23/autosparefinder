"""Redacted owner-WhatsApp messages captured from the bridge cache (2026-11-10..23), used as regression fixtures
(audit 2026-11-24). No customer data; supplier domains kept (business facts)."""

FLARE_TEXTS = [
    ("🔴 [🛰️ קטלוג וקצירה] שאיבה: FlareSolverr נכשל %d פעמים ברציפות\n\nכל ניסיונות קבלת cf_clearance נכשלו — "
     "Chrome קורס (OOM?) או FlareSolverr בעייתי.\nהקטלוג לא מתקדם.\nפעולה — הוסף swap לשרת:\n"
     "fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile") % n
    for n in (10, 13, 17, 19, 11, 14)
]

DB_AGENT_BODY = (
    "🆕 *חדש:*\n"
    "  • dedup_catalog_parts: -8ea8faa5dbc7', 'fff4e30a-5a27-4323-b3d0-ce2f498456b1'],)]\n"
    "(Background on this error at: https://sqlalche.me/e/20/dbapi)\n"
    "  • fix_manufacturer_overflow: AND is_active = TRUE\n"
    "            RETURNING id\n"
    "        ]\n"
    "(Background on this error at: https://sqlalche.me/e/20/dbapi)\n"
    "  • flag_fake_skus: Task timeout after 1800s\n"
    "(15 הצליחו · 5625s)"
)

DISK_SAMPLES = [  # the two alerts sent at 09:01 on 16/11: same numbers, two mount points
    {"path": "/", "total": 206_900_000_000, "used": 165_700_000_000, "free": 41_200_000_000, "pct": 80.1},
    {"path": "/app/state", "total": 206_900_000_000, "used": 165_700_000_000, "free": 41_200_000_000, "pct": 80.1},
]

ASAP_NEWSLETTER = [  # ten alerts for one sender/subject, each a new mail id
    {"id": f"{i:08x}-aaaa-bbbb-cccc-000000000000", "sender_email": "amanda@asapnetwork.org",
     "subject": "Product Updates", "classification": "supplier", "requires_human": True,
     "reason": "authenticated sender domain matches exactly one supplier website"}
    for i in range(1, 11)
]

NOA_DAILY_ENGLISH = (
    "NOA — Daily Facebook Activity\n\nGROUPS\n- Relevant posts identified: 20\n- Draft replies created: 20\n"
    "- Replies approved: 0\n- Replies actually published: 0\n- Failed/pending: 20\n\nPAGE\n- Relevant feed items identified: 0\n"
)
