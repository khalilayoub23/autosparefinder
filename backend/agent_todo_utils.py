from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_ACTIVE_TODO_SQL = text(
    """
    SELECT
        id::text AS id,
        title,
        description,
        status,
        priority,
        progress_pct,
        progress_notes,
        category,
        COALESCE(tags, ARRAY[]::varchar[]) AS tags,
        COALESCE(artifacts, '{}'::jsonb) AS artifacts
    FROM agent_todos
    WHERE assigned_to_agent = :agent_name
      AND status IN ('not_started', 'in_progress')
    ORDER BY
        CASE priority
            WHEN 'critical' THEN 1
            WHEN 'high' THEN 2
            WHEN 'medium' THEN 3
            ELSE 4
        END,
        created_at ASC
    """
)


async def get_active_agent_todos(db: AsyncSession, agent_name: str) -> List[Dict[str, Any]]:
    rows = (await db.execute(_ACTIVE_TODO_SQL, {"agent_name": agent_name})).mappings().all()
    todos: List[Dict[str, Any]] = []
    for row in rows:
        todo = dict(row)
        todo["tags"] = list(todo.get("tags") or [])
        todo["artifacts"] = dict(todo.get("artifacts") or {})
        todos.append(todo)
    return todos


def extract_todo_task_names(todos: List[Dict[str, Any]]) -> List[str]:
    task_names: List[str] = []
    for todo in todos:
        artifacts = todo.get("artifacts") or {}
        raw_names = artifacts.get("task_names") or []
        if isinstance(raw_names, str):
            raw_names = [raw_names]
        for task_name in raw_names:
            if isinstance(task_name, str) and task_name and task_name not in task_names:
                task_names.append(task_name)
    return task_names


def todo_requests_ranked_first(todos: List[Dict[str, Any]]) -> bool:
    for todo in todos:
        tags = {str(tag).lower() for tag in (todo.get("tags") or [])}
        artifacts = todo.get("artifacts") or {}
        mode = str(artifacts.get("mode") or "").lower()
        if "priority" in tags or mode == "ranked_first":
            return True
    return False


def extract_todo_brands(todos: List[Dict[str, Any]]) -> List[str]:
    """Return deduplicated brand list from any todo's artifacts.brands field."""
    seen: set = set()
    brands: List[str] = []
    for todo in todos:
        artifacts = todo.get("artifacts") or {}
        raw = artifacts.get("brands") or []
        if isinstance(raw, str):
            raw = [raw]
        for b in raw:
            if isinstance(b, str) and b.strip() and b.strip() not in seen:
                seen.add(b.strip())
                brands.append(b.strip())
    return brands


def extract_todo_discovery_target(todos: List[Dict[str, Any]]) -> int | None:
    """Return the highest target from any todo's artifacts.target field."""
    best: int | None = None
    for todo in todos:
        artifacts = todo.get("artifacts") or {}
        val = artifacts.get("target")
        if val is not None:
            try:
                v = int(val)
                if best is None or v > best:
                    best = v
            except (ValueError, TypeError):
                pass
    return best
