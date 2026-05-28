from datetime import UTC, datetime

from ai_agent.database.models import Task, TaskDecision
from ai_agent.repositories._base_repository import BaseRepo


class TasksRepository(BaseRepo[Task]):
    _model = Task

    async def upsert_from_notion(
        self,
        notion_page_id: str,
        notion_task_id: int | None,
        title: str,
        project: str | None,
        status: str | None,
        url: str,
    ) -> tuple[Task, Task | None]:
        """Insert if new, otherwise update mutable fields.

        Returns: (current_task, previous_snapshot or None).
        `previous_snapshot` is a detached copy of the row BEFORE the update
        (useful for diffing). Pass-through bind for the upsert race-free flow.
        """
        existing = await Task.get_or_none(notion_page_id=notion_page_id)
        if existing is None:
            new = await Task.create(
                notion_page_id=notion_page_id,
                notion_task_id=notion_task_id,
                title=title,
                project=project,
                status=status,
                url=url,
            )
            return new, None

        snapshot = Task(
            id=existing.id,
            notion_page_id=existing.notion_page_id,
            notion_task_id=existing.notion_task_id,
            title=existing.title,
            project=existing.project,
            status=existing.status,
            url=existing.url,
            decision=existing.decision,
            first_seen_at=existing.first_seen_at,
            last_seen_at=existing.last_seen_at,
            decided_at=existing.decided_at,
            defer_until=existing.defer_until,
            notified_at=existing.notified_at,
        )
        existing.title = title
        existing.project = project
        existing.status = status
        await existing.save(update_fields=["title", "project", "status", "updated_at"])
        return existing, snapshot

    async def list_pending_notified_page_ids(self) -> set[str]:
        rows = await Task.filter(
            decision=TaskDecision.PENDING.value,
            notified_at__not_isnull=True,
        ).values_list("notion_page_id", flat=True)
        return set(rows)

    async def mark_notified(self, notion_page_id: str) -> None:
        await Task.filter(notion_page_id=notion_page_id).update(notified_at=datetime.now(UTC))

    async def set_decision(self, notion_page_id: str, decision: TaskDecision) -> None:
        await Task.filter(notion_page_id=notion_page_id).update(
            decision=decision.value,
            decided_at=datetime.now(UTC),
        )

    async def list_by_decision(self, decision: TaskDecision) -> list[Task]:
        return await Task.filter(decision=decision.value).order_by("-first_seen_at")

    async def count_by_decision(self) -> dict[str, int]:
        rows = await Task.all().values("decision")
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["decision"]] = counts.get(r["decision"], 0) + 1
        return counts

    async def get_by_page_id(self, notion_page_id: str) -> Task | None:
        return await Task.get_or_none(notion_page_id=notion_page_id)
