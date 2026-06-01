from loguru import logger

from ai_agent.clients import TelegramClient
from ai_agent.database.models import TaskDecision
from ai_agent.repositories import TasksRepository
from ai_agent.schemas import NotionTaskDTO, TaskChangesDTO
from ai_agent.services.task_decision_service import TaskDecisionService
from ai_agent.services.task_execution_service import TaskExecutionService
from ai_agent.settings import settings


class TaskIntakeService:
    """Handles the discovery side of the pipeline.

    For each poll result it:
      - upserts the task in DB
      - detects changes vs previous snapshot
      - notifies the user via Telegram (new / changed / removed)

    In autonomous mode (settings.auto_accept) it instead auto-accepts one
    pending task per poll while no run is in flight, rather than waiting for a
    Telegram ▶️ Взять.
    """

    def __init__(
        self,
        tasks_repository: TasksRepository,
        telegram_client: TelegramClient,
        decision_service: TaskDecisionService,
        execution_service: TaskExecutionService,
    ) -> None:
        self._tasks = tasks_repository
        self._tg = telegram_client
        self._decision = decision_service
        self._execution = execution_service

    async def process_poll_result(self, tasks: list[NotionTaskDTO]) -> None:
        before_ids = await self._tasks.list_pending_notified_page_ids()
        current_ids: set[str] = set()
        pending: list[NotionTaskDTO] = []

        for dto in tasks:
            current_ids.add(dto.page_id)
            task, previous = await self._tasks.upsert_from_notion(
                notion_page_id=dto.page_id,
                notion_task_id=dto.task_id,
                title=dto.title,
                project=dto.project,
                status=dto.status,
                url=dto.url,
            )

            if task.decision == TaskDecision.PENDING.value:
                if settings.auto_accept:
                    # Autonomous mode: collect pending tasks; one is accepted
                    # after the loop (paced to one in-flight run at a time).
                    pending.append(dto)
                    continue
                # Manual mode: notify for any pending task not yet notified.
                # Covers brand-new tasks AND tasks re-queued for retry after a
                # transient failure (reset_for_retry clears notified_at).
                if task.notified_at is None:
                    logger.info("task.notify page_id={pid} title={t}", pid=dto.page_id, t=dto.title)
                    try:
                        await self._tg.send_new_task_card(dto)
                        await self._tasks.mark_notified(dto.page_id)
                    except Exception:
                        logger.exception("telegram.notify_failed page_id={pid}", pid=dto.page_id)
                    continue

            if previous is None:
                continue

            changes = _diff(
                previous_title=previous.title,
                previous_status=previous.status,
                previous_project=previous.project,
                new=dto,
            )
            if not changes.is_empty():
                logger.info("task.changed page_id={pid}", pid=dto.page_id)
                try:
                    await self._tg.send_task_changed(dto, changes)
                except Exception:
                    logger.exception(
                        "telegram.notify_changed_failed page_id={pid}",
                        pid=dto.page_id,
                    )

        await self._handle_removed(before_ids - current_ids)
        if settings.auto_accept:
            await self._maybe_auto_accept(pending)

    async def _maybe_auto_accept(self, pending: list[NotionTaskDTO]) -> None:
        """Accept one pending task if no run is currently in flight."""

        if not pending or self._execution.is_busy:
            return
        dto = pending[0]
        logger.info("task.auto_accept page_id={pid} title={t}", pid=dto.page_id, t=dto.title)
        try:
            await self._tg.send_auto_taken(dto)
        except Exception:
            logger.exception("telegram.auto_taken_failed page_id={pid}", pid=dto.page_id)
        await self._decision.accept(dto.page_id)

    async def _handle_removed(self, missing_ids: set[str]) -> None:
        for page_id in missing_ids:
            task = await self._tasks.get_by_page_id(page_id)
            if task is None:
                continue
            logger.info("task.removed page_id={pid} title={t}", pid=page_id, t=task.title)
            try:
                await self._tg.send_task_removed(task)
                await self._tasks.set_decision(page_id, TaskDecision.REMOVED)
            except Exception:
                logger.exception("telegram.notify_removed_failed page_id={pid}", pid=page_id)


def _diff(
    previous_title: str,
    previous_status: str | None,
    previous_project: str | None,
    new: NotionTaskDTO,
) -> TaskChangesDTO:
    changes = TaskChangesDTO()
    if previous_title != new.title:
        changes.title = (previous_title, new.title)
    if previous_status != new.status:
        changes.status = (previous_status, new.status)
    if previous_project != new.project:
        changes.project = (previous_project, new.project)
    return changes
