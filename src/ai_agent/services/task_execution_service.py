"""Orchestrates a single Task → MR run.

Phase 1.9a: linear happy path, no iteration loops yet.
  PREPARING → CODING → TESTING → REVIEWING → PUSHING → AWAITING_APPROVAL

Iterations on gate / reviewer failures and the AWAITING_APPROVAL → MERGING
transition (triggered by Telegram /approve) are added in 1.9b / 1.10.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from loguru import logger

from ai_agent.ai import ClaudeRunner, ClaudeRunRequest, ReviewerAgent
from ai_agent.clients import GitLabClient, NotionClient, TelegramClient
from ai_agent.database.models import Task, TaskRun, TaskRunStatus
from ai_agent.errors import AgentError, NotFoundError
from ai_agent.gates import QualityGates
from ai_agent.git_ops import RepoManager, WorktreeManager
from ai_agent.git_ops.worktree import make_branch_slug
from ai_agent.repositories import TaskRunsRepository
from ai_agent.schemas import MRCreateRequest
from ai_agent.services.coder_prompt import build_coder_prompt, build_mr_description
from ai_agent.settings import settings


class TaskExecutionService:
    def __init__(
        self,
        task_runs_repository: TaskRunsRepository,
        claude_runner: ClaudeRunner,
        reviewer: ReviewerAgent,
        quality_gates: QualityGates,
        repo_manager: RepoManager,
        worktree_manager: WorktreeManager,
        gitlab_client: GitLabClient,
        telegram_client: TelegramClient,
        notion_client: NotionClient,
    ) -> None:
        self._task_runs = task_runs_repository
        self._claude = claude_runner
        self._reviewer = reviewer
        self._gates = quality_gates
        self._repo = repo_manager
        self._worktree = worktree_manager
        self._gitlab = gitlab_client
        self._tg = telegram_client
        self._notion = notion_client
        self._lock = asyncio.Lock()

    async def start(self, task: Task) -> None:
        """Kick off execution as a background task (fire-and-forget)."""

        asyncio.create_task(
            self._run_safe(task),
            name=f"exec-{task.notion_page_id[:8]}",
        )

    async def _run_safe(self, task: Task) -> None:
        async with self._lock:
            run = await self._task_runs.create_for_task(task)
            try:
                await self._execute(run, task)
            except AgentError as e:
                logger.error(
                    "execution.failed page={p} stage={s} detail={d}",
                    p=task.notion_page_id,
                    s=run.status,
                    d=e.details,
                )
                await self._task_runs.mark_failed(run, str(e))
                await self._tg.send_execution_failed(task, run, f"{e}: {e.details}")
            except Exception as e:
                logger.exception("execution.crashed page={p}", p=task.notion_page_id)
                await self._task_runs.mark_failed(run, repr(e))
                await self._tg.send_execution_failed(task, run, repr(e))

    async def _execute(self, run: TaskRun, task: Task) -> None:
        # 1) PREPARING — clone+worktree
        await self._set_status(run, TaskRunStatus.PREPARING)
        await self._tg.send_execution_progress(task, run, "🔧 готовлю worktree…")
        await self._repo.ensure_cloned()
        slug = make_branch_slug(task.notion_task_id, task.title)
        handle = await self._worktree.create(slug)
        await self._task_runs.set_worktree(run, str(handle.path), handle.branch)

        # 2) CODING — first claude pass
        await self._set_status(run, TaskRunStatus.CODING)
        await self._tg.send_execution_progress(task, run, "🤖 Claude пишет код…")
        body = await self._notion.fetch_page_body(task.notion_page_id)
        prompt = build_coder_prompt(task, body=body)
        coding = await self._claude.run(
            ClaudeRunRequest(
                cwd=handle.path,
                prompt=prompt,
                model=settings.coder_model,
                timeout_seconds=1800,
            ),
        )
        await self._task_runs.append_event(
            run,
            {
                "stage": "coding",
                "success": coding.success,
                "duration_s": coding.duration_seconds,
                "events": len(coding.events),
            },
        )
        if coding.rate_limited:
            raise AgentError(
                "rate limited during coding",
                details={"resets_at": coding.rate_limit_resets_at, "rate_limited": True},
            )
        if not coding.success:
            raise AgentError(
                "coder run failed",
                details={"error": coding.error, "stderr_tail": coding.stderr[-500:]},
            )
        run.iterations_code += 1
        await run.save(update_fields=["iterations_code", "updated_at"])

        # 3) TESTING — gates
        await self._set_status(run, TaskRunStatus.TESTING)
        await self._tg.send_execution_progress(task, run, "🧪 запускаю линтер и тесты…")
        gate_suite = await self._gates.run(handle.path)
        await self._task_runs.append_event(
            run,
            {
                "stage": "gates",
                "passed": gate_suite.all_passed,
                "failures": [r.name for r in gate_suite.failures],
            },
        )
        if not gate_suite.all_passed:
            raise AgentError(
                "quality gates failed (no iteration in 1.9a)",
                details={"failures": [r.name for r in gate_suite.failures]},
            )

        # 4) REVIEWING — reviewer agent
        await self._set_status(run, TaskRunStatus.REVIEWING)
        await self._tg.send_execution_progress(task, run, "🔎 reviewer проверяет diff…")
        diff = await self._worktree.diff_against_base(handle)
        verdict = await self._reviewer.review(
            worktree_path=handle.path,
            diff_text=diff,
            task_brief=task.title,
        )
        await self._task_runs.append_event(
            run,
            {
                "stage": "review",
                "verdict": verdict.verdict,
                "summary": verdict.summary,
                "blocking": len(verdict.blocking_issues),
            },
        )
        if not verdict.is_approved:
            raise AgentError(
                "reviewer requested changes (no iteration in 1.9a)",
                details={"summary": verdict.summary},
            )

        # 5) PUSHING — push branch + open MR
        await self._set_status(run, TaskRunStatus.PUSHING)
        await self._tg.send_execution_progress(task, run, "🚀 пушу ветку и открываю MR…")
        await self._worktree.push(handle)
        mr = await self._gitlab.create_merge_request(
            MRCreateRequest(
                source_branch=handle.branch,
                target_branch=self._repo.default_branch,
                title=f"[ai-agent] {task.title}",
                description=build_mr_description(task, verdict, gate_suite, task.url),
            )
        )
        await self._task_runs.set_mr(run, mr.iid, mr.web_url)

        # 6) AWAITING_APPROVAL — Phase 1.10 handles the merge callback
        await self._set_status(run, TaskRunStatus.AWAITING_APPROVAL)
        await self._tg.send_mr_ready(task, run, mr)

    async def _set_status(self, run: TaskRun, status: TaskRunStatus) -> None:
        await self._task_runs.set_status(run, status)
        logger.info(
            "execution.status page={p} status={s}",
            p=run.task_id if hasattr(run, "task_id") else "?",
            s=status.value,
        )

    async def approve_and_merge(self, run_id: UUID) -> None:
        run = await self._task_runs.get_or_none(id=run_id)
        if run is None:
            raise NotFoundError(details={"run_id": str(run_id)})
        if run.status != TaskRunStatus.AWAITING_APPROVAL.value:
            raise AgentError(
                "run is not awaiting approval",
                details={"status": run.status, "run_id": str(run_id)},
            )
        if run.mr_iid is None:
            raise AgentError("run has no MR to merge", details={"run_id": str(run_id)})

        task = await run.task
        try:
            await self._set_status(run, TaskRunStatus.MERGING)
            await self._tg.send_execution_progress(task, run, "merging MR…")
            mr = await self._gitlab.merge_merge_request(run.mr_iid)
            await self._cleanup_worktree(run)
            await self._task_runs.mark_done(run)
            await self._tg.send_run_finished(task, run, mr_url=mr.web_url, succeeded=True)
        except Exception as e:
            logger.exception("execution.merge_failed run={r}", r=str(run_id))
            await self._task_runs.mark_failed(run, repr(e))
            await self._tg.send_execution_failed(task, run, repr(e))

    async def cancel(self, run_id: UUID) -> None:
        run = await self._task_runs.get_or_none(id=run_id)
        if run is None:
            raise NotFoundError(details={"run_id": str(run_id)})
        task = await run.task
        try:
            await self._cleanup_worktree(run)
        except Exception:
            logger.exception("execution.cleanup_failed run={r}", r=str(run_id))
        run.status = TaskRunStatus.CANCELLED.value
        await run.save(update_fields=["status", "updated_at"])
        await self._tg.send_run_finished(task, run, mr_url=run.mr_url, succeeded=False)

    async def _cleanup_worktree(self, run: TaskRun) -> None:
        if not run.branch_name:
            return
        slug = run.branch_name.split("/", 1)[-1]
        await self._worktree.remove(slug)
