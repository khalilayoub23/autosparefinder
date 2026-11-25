"""
Script:  maintenance/nir_agent_run.py
Purpose: One-shot / status runner for the NIR agent (services/nir_agent.py). Runs the SAME run_task/run_agent_cycle the
         supervised loop uses, so it is both the operator's tool and the production-safe way to prove the agent works.
Process: --status            counts of NIR tasks by agent state + last cycle (reads agent_todos only; no LLM, no mail)
         --task <id>         run ONE task now (LLM decisions + real tools); prints every step
         --cycle [--batch N] one agent cycle (due tasks, reassessment seeding)
         --allow-send        let request_outreach use the existing gated send path (default: read-only Gmail)
Data Imported/Modified: agent_todos.artifacts.nir_agent, suppliers.credentials (evidence), supplier_outreach.state.
Last Updated: 2026-11-23
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _status():
    from services import nir_agent as A
    rows = await A.PgRepo().due_tasks(500, A._now().replace(year=2100))
    by = {}
    for t in rows:
        by[t["state"].get("state", "new")] = by.get(t["state"].get("state", "new"), 0) + 1
    print(json.dumps({"open_nir_tasks": len(rows), "by_state": by,
                      "tasks": [{"id": t["id"][:8], "kind": t["state"]["kind"], "state": t["state"].get("state"),
                                 "runs": t["state"]["runs"], "next_review_at": t["state"].get("next_review_at"),
                                 "title": (t["title"] or "")[:60]} for t in rows]}, indent=1, default=str))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--task")
    ap.add_argument("--cycle", action="store_true")
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--allow-send", action="store_true")
    a = ap.parse_args()
    from services import nir_agent as A
    if a.status:
        return await _status()
    deps = A.build_prod_deps(allow_send=a.allow_send)
    # Same lock as the in-process loop (BACKEND_API_ROUTES._nir_contact_enrichment_loop): never two NIR cycles at once.
    from BACKEND_AUTH_SECURITY import get_redis
    from distributed_lock import acquire_lock
    lock = await acquire_lock(await get_redis(), "nir_agent_cycle", ttl_seconds=1800)
    if not lock:
        print("another NIR cycle is running (lock autospare:lock:nir_agent_cycle) - not starting a second one"); return
    try:
        await _run(a, A, deps)
    finally:
        await lock.release()


async def _run(a, A, deps):
    if a.task:
        far = A._now().replace(year=2100)
        task = next((t for t in await deps.repo.due_tasks(500, far) if t["id"].startswith(a.task)), None)
        if not task:
            print("task not found among open NIR tasks"); return
        res = await A.run_task(task, deps)
        print(json.dumps({"result": res, "steps": task["state"]["steps"]}, indent=1, ensure_ascii=False, default=str))
    elif a.cycle:
        print(json.dumps(await A.run_agent_cycle(deps, batch=a.batch), indent=1, default=str))


asyncio.run(main())
