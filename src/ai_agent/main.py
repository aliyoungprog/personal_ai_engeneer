from __future__ import annotations

import asyncio
import logging
import signal
import sys

import structlog

from ai_agent.config import settings
from ai_agent.notion.poller import NotionPoller, NotionTask
from ai_agent.storage.state import StateStore
from ai_agent.telegram.bot import TelegramService


def _setup_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO),
        ),
    )


log = structlog.get_logger()


async def run() -> None:
    _setup_logging()
    log.info("startup", version="0.1.0", db=settings.database_path)

    store = StateStore(settings.database_path)
    await store.init()

    poller = NotionPoller(
        token=settings.notion_token,
        data_source_id=settings.notion_data_source_id,
        assignee_name=settings.notion_assignee_name,
        projects=settings.notion_project_whitelist,
        statuses=settings.notion_status_whitelist,
    )

    tg = TelegramService(
        token=settings.telegram_bot_token,
        allowed_user_id=settings.telegram_allowed_user_id,
        store=store,
    )

    async def on_tasks(tasks: list[NotionTask]) -> None:
        for t in tasks:
            is_new = await store.upsert_seen(
                notion_page_id=t.page_id,
                task_id=t.task_id,
                title=t.title,
                project=t.project,
                status=t.status,
                url=t.url,
            )
            if is_new:
                log.info("task.new", page_id=t.page_id, title=t.title)
                try:
                    await tg.notify_new_task(t)
                except Exception:  # noqa: BLE001
                    log.exception("telegram.notify_failed", page_id=t.page_id)

    async def poll_loop() -> None:
        while True:
            try:
                tasks = await poller.fetch_tasks()
                log.info("notion.poll", count=len(tasks))
                await on_tasks(tasks)
            except Exception:  # noqa: BLE001
                log.exception("notion.poll_failed")
            await asyncio.sleep(settings.poll_interval_seconds)

    stop_event = asyncio.Event()

    def _request_stop(*_: object) -> None:
        log.info("shutdown.requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass

    bot_task = asyncio.create_task(tg.start(), name="telegram")
    poll_task = asyncio.create_task(poll_loop(), name="poller")
    stop_task = asyncio.create_task(stop_event.wait(), name="stop")

    done, pending = await asyncio.wait(
        {bot_task, poll_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
    for t in done:
        if t is not stop_task and not t.cancelled():
            exc = t.exception()
            if exc:
                log.error("task.crashed", name=t.get_name(), error=str(exc))

    await tg.close()
    await poller.close()
    log.info("shutdown.complete")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
