from loguru import logger

from ai_agent.database.models import Task


class TaskExecutionService:
    """Phase 1 stub: orchestrates coding-agent runs.

    Will own:
      - git worktree management
      - Claude Code runner subprocess
      - quality gates (lint/tests/types)
      - reviewer agent
      - GitLab MR creation + merge
      - FSM state in `task_runs` table
    """

    async def start(self, task: Task) -> None:
        logger.warning(
            "execution.not_implemented page_id={pid} title={t} — Phase 1 pending",
            pid=task.notion_page_id,
            t=task.title,
        )
