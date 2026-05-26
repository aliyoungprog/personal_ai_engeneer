from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import aiosqlite

TaskDecision = Literal["pending", "accepted", "skipped", "deferred"]


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    notion_page_id TEXT PRIMARY KEY,
    task_id        INTEGER,
    title          TEXT NOT NULL,
    project        TEXT,
    status         TEXT,
    url            TEXT NOT NULL,
    decision       TEXT NOT NULL DEFAULT 'pending',
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    decided_at     TEXT,
    defer_until    TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_decision ON tasks(decision);
"""


@dataclass(slots=True)
class TaskRow:
    notion_page_id: str
    task_id: int | None
    title: str
    project: str | None
    status: str | None
    url: str
    decision: TaskDecision
    first_seen_at: str
    last_seen_at: str
    decided_at: str | None
    defer_until: str | None


class StateStore:
    def __init__(self, path: str) -> None:
        self._path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def upsert_seen(
        self,
        notion_page_id: str,
        task_id: int | None,
        title: str,
        project: str | None,
        status: str | None,
        url: str,
    ) -> bool:
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                "SELECT decision FROM tasks WHERE notion_page_id = ?",
                (notion_page_id,),
            )
            row = await cur.fetchone()
            if row is None:
                await db.execute(
                    """
                    INSERT INTO tasks (notion_page_id, task_id, title, project, status, url,
                                       decision, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (notion_page_id, task_id, title, project, status, url, now, now),
                )
                await db.commit()
                return True
            await db.execute(
                "UPDATE tasks SET last_seen_at = ?, title = ?, status = ? WHERE notion_page_id = ?",
                (now, title, status, notion_page_id),
            )
            await db.commit()
            return False

    async def set_decision(self, notion_page_id: str, decision: TaskDecision) -> None:
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "UPDATE tasks SET decision = ?, decided_at = ? WHERE notion_page_id = ?",
                (decision, now, notion_page_id),
            )
            await db.commit()

    async def list_by_decision(self, decision: TaskDecision) -> list[TaskRow]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM tasks WHERE decision = ? ORDER BY first_seen_at DESC",
                (decision,),
            )
            rows = await cur.fetchall()
            return [TaskRow(**dict(r)) for r in rows]

    async def get(self, notion_page_id: str) -> TaskRow | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM tasks WHERE notion_page_id = ?",
                (notion_page_id,),
            )
            row = await cur.fetchone()
            return TaskRow(**dict(row)) if row else None
