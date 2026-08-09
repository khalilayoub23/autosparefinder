"""
integrations/facebook_browser/task_queue.py — Serialized task queue for browser actions.

Playwright sessions can't be shared across concurrent callers — two tasks
writing to the same group at the same time will corrupt each other's
DOM state. This module provides an async task queue that serializes all
browser actions and adds retry logic.

Usage:
    from integrations.facebook_browser.task_queue import BrowserTaskQueue

    queue = BrowserTaskQueue()
    result = await queue.submit(
        action="group_comment",
        group_url="https://facebook.com/groups/...",
        post_url="https://facebook.com/...",
        comment_text="...",
    )
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger("integrations.facebook_browser.task_queue")

# Maximum concurrent browser sessions (must match HARVESTER_PARALLEL_SESSIONS constraints)
_MAX_CONCURRENT = 1
_MAX_RETRIES = 2


@dataclass
class BrowserTask:
    task_id: str
    action: str  # "group_scan" | "group_comment" | "group_publish"
    params: dict
    retries: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    result: Any = None
    error: str | None = None
    status: str = "pending"  # pending | running | done | failed


class BrowserTaskQueue:
    """Serialized async queue for browser actions.

    Single-instance (singleton via module-level _instance). All callers
    share one queue so sessions never overlap.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[BrowserTask] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
        self._tasks: dict[str, BrowserTask] = {}
        self._worker_started = False

    async def submit(self, action: str, **params: Any) -> BrowserTask:
        """Enqueue a browser task and wait for its result."""
        task = BrowserTask(
            task_id=str(uuid.uuid4())[:8],
            action=action,
            params=params,
        )
        self._tasks[task.task_id] = task
        await self._queue.put(task)
        log.info("BrowserTaskQueue: enqueued %s action=%s", task.task_id, action)

        # Start the worker loop if not already running
        if not self._worker_started:
            self._worker_started = True
            asyncio.create_task(self._worker())

        # Wait for the task to complete (with timeout)
        deadline = 180  # 3 minutes max for any browser action
        waited = 0
        while task.status in ("pending", "running") and waited < deadline:
            await asyncio.sleep(1)
            waited += 1

        if task.status == "pending":
            task.status = "failed"
            task.error = "timeout waiting for browser worker"

        return task

    async def _worker(self) -> None:
        """Drain the queue one task at a time."""
        while True:
            try:
                task = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                self._worker_started = False
                break

            task.status = "running"
            async with self._semaphore:
                try:
                    task.result = await self._execute(task)
                    task.status = "done"
                    log.info("BrowserTaskQueue: task %s done", task.task_id)
                except Exception as exc:
                    if task.retries < _MAX_RETRIES:
                        task.retries += 1
                        task.status = "pending"
                        await self._queue.put(task)
                        log.warning("BrowserTaskQueue: task %s retry %d: %s",
                                    task.task_id, task.retries, exc)
                    else:
                        task.status = "failed"
                        task.error = str(exc)[:200]
                        log.error("BrowserTaskQueue: task %s failed: %s", task.task_id, exc)

            self._queue.task_done()

    async def _execute(self, task: BrowserTask) -> Any:
        """Dispatch to the correct browser agent method."""
        from social.facebook_browser import GroupAgent
        async with GroupAgent() as agent:
            if task.action == "group_scan":
                approved_groups = task.params.get("approved_groups", [])
                return await agent.scan_groups(approved_groups)

            elif task.action == "group_comment":
                return await agent.submit_approved_comment(
                    post_url=task.params["post_url"],
                    comment_text=task.params["comment_text"],
                    group_url=task.params.get("group_url", ""),
                )

            elif task.action == "group_publish":
                return await agent.publish_group_post(
                    group_url=task.params["group_url"],
                    content=task.params["content"],
                )

            else:
                raise ValueError(f"unknown action: {task.action!r}")

    def get_status(self) -> dict:
        """Return queue stats for the admin dashboard."""
        by_status: dict[str, int] = {}
        for t in self._tasks.values():
            by_status[t.status] = by_status.get(t.status, 0) + 1
        return {
            "queue_size": self._queue.qsize(),
            "total_tasks": len(self._tasks),
            "by_status": by_status,
            "worker_active": self._worker_started,
        }


# Module-level singleton — all callers share one queue
_instance: BrowserTaskQueue | None = None


def get_queue() -> BrowserTaskQueue:
    global _instance
    if _instance is None:
        _instance = BrowserTaskQueue()
    return _instance
