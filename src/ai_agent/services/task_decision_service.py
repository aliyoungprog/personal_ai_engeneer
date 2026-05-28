from loguru import logger

from ai_agent.database.models import TaskDecision
from ai_agent.repositories import TasksRepository
from ai_agent.services.task_execution_service import TaskExecutionService


class TaskDecisionService:
    """Handles user-driven decisions on tasks (accept/skip/defer)."""

    def __init__(
        self,
        tasks_repository: TasksRepository,
        execution_service: TaskExecutionService,
    ) -> None:
        self._tasks = tasks_repository
        self._execution = execution_service

    async def accept(self, notion_page_id: str) -> None:
        logger.info("decision.accept page_id={pid}", pid=notion_page_id)
        await self._tasks.set_decision(notion_page_id, TaskDecision.ACCEPTED)
        task = await self._tasks.get_by_page_id(notion_page_id)
        if task is not None:
            await self._execution.start(task)

    async def skip(self, notion_page_id: str) -> None:
        logger.info("decision.skip page_id={pid}", pid=notion_page_id)
        await self._tasks.set_decision(notion_page_id, TaskDecision.SKIPPED)

    async def defer(self, notion_page_id: str) -> None:
        logger.info("decision.defer page_id={pid}", pid=notion_page_id)
        await self._tasks.set_decision(notion_page_id, TaskDecision.DEFERRED)
