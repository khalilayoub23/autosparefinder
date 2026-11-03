# STUB app for the isolated startup-order repro: records when it serves and which PII revision it sees.
import os, time, psycopg2
def _rev():
    try:
        c = psycopg2.connect(os.environ["PG_DSN_PII"]); cur = c.cursor()
        cur.execute("SELECT version_num FROM alembic_version"); r = cur.fetchone()[0]; c.close(); return r
    except Exception as e: return f"ERR:{type(e).__name__}"
open("/tmp/out/app_started", "w").write(f"{time.time()} rev={_rev()}\n")
async def app(scope, receive, send):
    if scope["type"] != "http": return
    with open("/tmp/out/served", "a") as f: f.write(f"{time.time()} {scope['path']} rev={_rev()}\n")
    await send({"type": "http.response.start", "status": 200, "headers": []}); await send({"type": "http.response.body", "body": b"ok"})
