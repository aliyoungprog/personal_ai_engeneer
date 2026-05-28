import asyncio

from loguru import logger

from ai_agent.clients import NotionClient
from ai_agent.services import TaskIntakeService


class NotionPollerInterface:
    """Periodically polls Notion and delegates results to the intake service."""

    def __init__(
        self,
        notion_client: NotionClient,
        intake_service: TaskIntakeService,
        interval_seconds: int,
    ) -> None:
        self._client = notion_client
        self._intake = intake_service
        self._interval = interval_seconds
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        logger.info("notion.poller.start interval={i}s", i=self._interval)
        while not self._stopping.is_set():
            try:
                tasks = await self._client.fetch_tasks()
                logger.info("notion.poll count={c}", c=len(tasks))
                await self._intake.process_poll_result(tasks)
            except Exception:
                logger.exception("notion.poll_failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)
            except TimeoutError:
                continue

    def request_stop(self) -> None:
        self._stopping.set()
