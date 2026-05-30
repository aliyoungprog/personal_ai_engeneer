"""Demo the PRODUCTION execution path — the exact code the bot runs on accept.

Builds the real TaskExecutionService from the DI container, inserts a Task
into the DB, and runs it through the full FSM. Set EXECUTION_DRY_RUN=true to
stop after REVIEWING (no push / MR). Run detached so it isn't tied to a
docker-exec session:

    docker exec ai-agent bash -c \
      'setsid python -u scripts/trigger_via_service.py > /tmp/trigger.log 2>&1 < /dev/null &'
"""

from __future__ import annotations

import asyncio

from loguru import logger

from ai_agent.database import database_connect, database_disconnect
from ai_agent.database.models import Task, TaskRunStatus
from ai_agent.dependencies import get_container
from ai_agent.utils.logging import configure_logging

SAFE_TITLE = "Add scratch/agent_demo/temperature.py with c<->f conversion + tests"
SAFE_PAGE_ID = "demo-service-trigger-0001"


async def main() -> None:
    configure_logging("INFO")
    await database_connect()
    container = get_container()
    execution = container.task_execution_service()

    # Insert (or reuse) a synthetic Task — same shape the poller would store.
    task = await Task.get_or_none(notion_page_id=SAFE_PAGE_ID)
    if task is None:
        task = await Task.create(
            notion_page_id=SAFE_PAGE_ID,
            notion_task_id=4242,
            title=SAFE_TITLE,
            project="AI Platform",
            status="To Do",
            url="https://example.invalid/demo",
            decision="accepted",
        )
    logger.info("trigger.start page={p} title={t}", p=task.notion_page_id, t=task.title)

    # This is exactly what the bot calls on the Telegram 'accept' callback.
    await execution.start(task)

    # start() fires a background task; poll the run row until it settles.
    runs_repo = container.task_runs_repository()
    terminal = {
        TaskRunStatus.DONE.value,
        TaskRunStatus.FAILED.value,
        TaskRunStatus.CANCELLED.value,
        TaskRunStatus.AWAITING_APPROVAL.value,
    }
    for _ in range(240):  # up to ~20 min
        await asyncio.sleep(5)
        run = await runs_repo.latest_for_task(task)
        if run is None:
            continue
        logger.info("trigger.poll status={s}", s=run.status)
        if run.status in terminal:
            logger.info(
                "trigger.settled status={s} branch={b} mr={m} err={e}",
                s=run.status,
                b=run.branch_name,
                m=run.mr_url,
                e=run.error_message,
            )
            logger.info("trigger.events {e}", e=run.events)
            break

    await database_disconnect()


if __name__ == "__main__":
    asyncio.run(main())
