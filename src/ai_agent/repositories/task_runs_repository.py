from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ai_agent.database.models import Task, TaskRun, TaskRunStatus
from ai_agent.repositories._base_repository import BaseRepo


class TaskRunsRepository(BaseRepo[TaskRun]):
    _model = TaskRun

    async def create_for_task(self, task: Task) -> TaskRun:
        return await TaskRun.create(task=task)

    async def latest_for_task(self, task: Task) -> TaskRun | None:
        return await TaskRun.filter(task=task).order_by("-started_at").first()

    async def set_status(self, run: TaskRun, status: TaskRunStatus) -> None:
        run.status = status.value
        await run.save(update_fields=["status", "updated_at"])

    async def set_worktree(self, run: TaskRun, path: str, branch: str) -> None:
        run.worktree_path = path
        run.branch_name = branch
        await run.save(update_fields=["worktree_path", "branch_name", "updated_at"])

    async def set_mr(self, run: TaskRun, iid: int, url: str) -> None:
        run.mr_iid = iid
        run.mr_url = url
        await run.save(update_fields=["mr_iid", "mr_url", "updated_at"])

    async def mark_failed(self, run: TaskRun, error: str) -> None:
        run.status = TaskRunStatus.FAILED.value
        run.error_message = error
        run.finished_at = datetime.now(UTC)
        await run.save(
            update_fields=["status", "error_message", "finished_at", "updated_at"],
        )

    async def mark_done(self, run: TaskRun) -> None:
        run.status = TaskRunStatus.DONE.value
        run.finished_at = datetime.now(UTC)
        await run.save(update_fields=["status", "finished_at", "updated_at"])

    async def append_event(self, run: TaskRun, event: dict[str, Any]) -> None:
        events = list(run.events or [])
        events.append({**event, "at": datetime.now(UTC).isoformat()})
        run.events = events
        await run.save(update_fields=["events", "updated_at"])
