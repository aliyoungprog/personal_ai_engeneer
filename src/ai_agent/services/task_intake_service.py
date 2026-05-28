from loguru import logger

from ai_agent.clients import TelegramClient
from ai_agent.database.models import TaskDecision
from ai_agent.repositories import TasksRepository
from ai_agent.schemas import NotionTaskDTO, TaskChangesDTO


class TaskIntakeService:
    """Handles the discovery side of the pipeline.

    For each poll result it:
      - upserts the task in DB
      - detects changes vs previous snapshot
      - notifies the user via Telegram (new / changed / removed)
    """

    def __init__(
        self,
        tasks_repository: TasksRepository,
        telegram_client: TelegramClient,
    ) -> None:
        self._tasks = tasks_repository
        self._tg = telegram_client

    async def process_poll_result(self, tasks: list[NotionTaskDTO]) -> None:
        before_ids = await self._tasks.list_pending_notified_page_ids()
        current_ids: set[str] = set()

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

            if previous is None:
                if task.notified_at is None and task.decision == TaskDecision.PENDING.value:
                    logger.info("task.notify page_id={pid} title={t}", pid=dto.page_id, t=dto.title)
                    try:
                        await self._tg.send_new_task_card(dto)
                        await self._tasks.mark_notified(dto.page_id)
                    except Exception:
                        logger.exception("telegram.notify_failed page_id={pid}", pid=dto.page_id)
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
