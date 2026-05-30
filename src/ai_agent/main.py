import asyncio
import signal

from loguru import logger

from ai_agent.database import database_connect, database_disconnect
from ai_agent.dependencies import (
    get_notion_client,
    get_notion_poller,
    get_telegram_client,
    get_telegram_interface,
)
from ai_agent.settings import settings
from ai_agent.utils.logging import configure_logging


async def run() -> None:
    configure_logging(settings.log_level)
    logger.info("startup version=0.1.0 db={d}", d=settings.database_url.split("@")[-1])

    await database_connect()

    telegram = get_telegram_interface()
    poller = get_notion_poller()

    if settings.demo_task_on_start:
        await _inject_demo_task()

    stop_event = asyncio.Event()

    def _request_stop(*_: object) -> None:
        logger.info("shutdown.requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass

    bot_task = asyncio.create_task(telegram.start(), name="telegram")
    poll_task = asyncio.create_task(poller.start(), name="poller")
    stop_task = asyncio.create_task(stop_event.wait(), name="stop")

    _done, pending = await asyncio.wait(
        {bot_task, poll_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    poller.request_stop()
    for t in pending:
        t.cancel()

    await get_telegram_client().close()
    await get_notion_client().close()
    await database_disconnect()
    logger.info("shutdown.complete")


async def _inject_demo_task() -> None:
    """Debug hook: create one synthetic accepted task and run it via the real
    execution service from the PID-1 process (bypasses Telegram + docker exec)."""

    from ai_agent.database.models import Task
    from ai_agent.dependencies import get_container

    page_id = "demo-pid1-trigger-0001"
    task = await Task.get_or_none(notion_page_id=page_id)
    if task is None:
        task = await Task.create(
            notion_page_id=page_id,
            notion_task_id=4242,
            title="Add scratch/agent_demo/temperature.py with c<->f conversion + tests",
            project="AI Platform",
            status="To Do",
            url="https://example.invalid/demo",
            decision="accepted",
        )
    logger.info("demo.inject page={p}", p=task.notion_page_id)
    await get_container().task_execution_service().start(task)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
