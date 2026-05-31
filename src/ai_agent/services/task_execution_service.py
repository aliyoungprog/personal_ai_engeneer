"""Orchestrates a single Task → MR run.

  PREPARING → [ CODING → TESTING → REVIEWING ]* → PUSHING → AWAITING_APPROVAL

The CODING → TESTING → REVIEWING block iterates: when the quality gates fail or
the reviewer requests changes, their feedback is fed back to the coder for a
fresh fix pass (in the same worktree, building on the prior commits), up to
`settings.max_iterations` passes. The run fails only if it is still unhappy
after the last pass. The AWAITING_APPROVAL → MERGING transition is triggered by
a Telegram /approve.
"""

from __future__ import annotations

import asyncio
from collections import deque
from html import escape
from time import monotonic
from typing import Any
from uuid import UUID

from loguru import logger

from ai_agent.ai import ClaudeRunner, ClaudeRunRequest, ReviewerAgent, ReviewVerdict
from ai_agent.clients import GitLabClient, NotionClient, TelegramClient
from ai_agent.database.models import Task, TaskRun, TaskRunStatus
from ai_agent.errors import AgentError, NotFoundError
from ai_agent.gates import GateSuite, QualityGates
from ai_agent.git_ops import RepoManager, WorktreeManager
from ai_agent.git_ops.worktree import WorktreeHandle, make_branch_slug
from ai_agent.repositories import TaskRunsRepository, TasksRepository
from ai_agent.schemas import MRCreateRequest
from ai_agent.services.coder_prompt import (
    build_coder_prompt,
    build_fix_prompt,
    build_mr_description,
)
from ai_agent.settings import settings

_TOOL_ICONS = {"Edit": "✏️", "Write": "📝", "MultiEdit": "✏️", "Read": "👁", "Bash": "$"}

# Substrings that mark a failure as transient/infrastructure (network, VPN, DNS)
# rather than a real coding problem — these are safe to auto-retry.
_TRANSIENT_MARKERS = (
    "could not connect to server",
    "could not resolve host",
    "failed to connect",
    "connection refused",
    "connection timed out",
    "network is unreachable",
    "unable to access",
    "operation timed out",
    "temporary failure in name resolution",
)


def _is_transient_failure(error: AgentError) -> bool:
    """True if an error looks like a connectivity blip (e.g. VPN down)."""

    haystack = f"{error} {error.details}".lower()
    return any(marker in haystack for marker in _TRANSIENT_MARKERS)


def _format_stream_event(event: dict[str, Any]) -> list[str]:
    """Turn one Claude stream-json event into short human display lines.

    Returns [] for events with nothing worth showing (system/result/etc).
    Lines are plain text — the caller HTML-escapes them.
    """

    if event.get("type") != "assistant":
        return []
    content = event.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return []
    lines: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = " ".join(block.get("text", "").split())
            if text:
                lines.append(f"💬 {text[:120]}")
        elif btype == "tool_use":
            name = block.get("name", "?")
            inp = block.get("input", {}) if isinstance(block.get("input"), dict) else {}
            if name in ("Edit", "Write", "MultiEdit", "Read"):
                fp = str(inp.get("file_path", "")).rsplit("/", 1)[-1]
                lines.append(f"{_TOOL_ICONS[name]} {name} {fp}".rstrip())
            elif name == "Bash":
                cmd = " ".join(str(inp.get("command", "")).split())
                lines.append(f"$ {cmd[:80]}")
            else:
                lines.append(f"🔧 {name}")
    return lines


class _CodingStream:
    """Accumulates claude events into one throttled, edited-in-place TG message.

    Used for both the coder and the reviewer stage (different `prefix`).
    """

    def __init__(
        self, tg: TelegramClient, task: Task, min_interval: float, *, prefix: str = "🧑‍💻"
    ) -> None:
        self._tg = tg
        self._prefix = prefix
        self._tid = f"T-{task.notion_task_id}" if task.notion_task_id else "?"
        self._title = task.title[:60]
        self._min_interval = min_interval
        self._message_id: int | None = None
        self._last_edit = 0.0
        self._lines: deque[str] = deque(maxlen=12)

    def _render(self, footer: str = "") -> str:
        head = f"{self._prefix} <b>{escape(self._tid)}</b> {escape(self._title)}"
        body = "\n".join(escape(line) for line in self._lines) or "…"
        return f"{head}\n{body}{footer}"

    async def start(self) -> None:
        self._message_id = await self._tg.send_live(self._render())
        self._last_edit = monotonic()

    async def __call__(self, event: dict[str, Any]) -> None:
        new = _format_stream_event(event)
        if not new:
            return
        self._lines.extend(new)
        if monotonic() - self._last_edit < self._min_interval:
            return
        await self._flush()

    async def _flush(self, footer: str = "") -> None:
        self._last_edit = monotonic()
        if self._message_id is None:
            self._message_id = await self._tg.send_live(self._render(footer))
        else:
            await self._tg.edit_live(self._message_id, self._render(footer))

    async def finalize(self, footer: str) -> None:
        await self._flush(footer=f"\n{footer}")


class TaskExecutionService:
    def __init__(
        self,
        task_runs_repository: TaskRunsRepository,
        tasks_repository: TasksRepository,
        claude_runner: ClaudeRunner,
        reviewer: ReviewerAgent,
        quality_gates: QualityGates,
        repo_manager: RepoManager,
        worktree_manager: WorktreeManager,
        gitlab_client: GitLabClient,
        telegram_client: TelegramClient,
        notion_client: NotionClient,
        dry_run: bool = False,
    ) -> None:
        self._task_runs = task_runs_repository
        self._tasks = tasks_repository
        self._claude = claude_runner
        self._reviewer = reviewer
        self._gates = quality_gates
        self._repo = repo_manager
        self._worktree = worktree_manager
        self._gitlab = gitlab_client
        self._tg = telegram_client
        self._notion = notion_client
        self._dry_run = dry_run
        self._lock = asyncio.Lock()

    async def _notify(self, task: Task, run: TaskRun, line: str) -> None:
        """Best-effort progress ping; a Telegram outage must not abort a run."""

        try:
            await self._tg.send_execution_progress(task, run, line)
        except Exception:
            logger.warning("execution.notify_failed page={p}", p=task.notion_page_id)

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
                transient = _is_transient_failure(e)
                logger.error(
                    "execution.failed page={p} stage={s} transient={t} detail={d}",
                    p=task.notion_page_id,
                    s=run.status,
                    t=transient,
                    d=e.details,
                )
                await self._task_runs.mark_failed(run, str(e))
                await self._handle_failure(task, run, f"{e}: {e.details}", transient=transient)
            except Exception as e:
                logger.exception("execution.crashed page={p}", p=task.notion_page_id)
                await self._task_runs.mark_failed(run, repr(e))
                await self._handle_failure(task, run, repr(e), transient=False)

    async def _handle_failure(
        self, task: Task, run: TaskRun, reason: str, *, transient: bool
    ) -> None:
        # A transient/infrastructure failure (e.g. VPN down → cannot reach the
        # git host) is not the task's fault: roll the decision back to PENDING
        # and clear the notification so the poller re-offers it once connectivity
        # returns. Real failures (coder/gates/reviewer) stay failed but still get
        # a manual retry button.
        if transient:
            await self._tasks.reset_for_retry(task.notion_page_id)
            logger.info("execution.retry_queued page={p}", p=task.notion_page_id)
        try:
            await self._tg.send_execution_failed(task, run, reason, transient=transient)
        except Exception:
            logger.warning("execution.notify_failed_failed page={p}", p=task.notion_page_id)

    async def _drop_lockfile_noise(self, handle: WorktreeHandle, run: TaskRun) -> None:
        """Revert uv.lock if it changed without a corresponding pyproject change."""

        changed = await self._worktree.changed_files(handle)
        if "uv.lock" not in changed or "pyproject.toml" in changed:
            return
        reverted = await self._worktree.revert_file_to_base(
            handle,
            "uv.lock",
            message="chore: restore uv.lock (no dependency change)",
        )
        if reverted:
            logger.info("execution.lockfile_reverted run={r}", r=str(run.id))
            await self._task_runs.append_event(run, {"stage": "lockfile_revert", "file": "uv.lock"})

    async def _execute(self, run: TaskRun, task: Task) -> None:
        # 1) PREPARING — clone+worktree
        await self._set_status(run, TaskRunStatus.PREPARING)
        await self._notify(task, run, "готовлю worktree")
        await self._repo.ensure_cloned()
        slug = make_branch_slug(task.notion_task_id, task.title)
        handle = await self._worktree.create(slug)
        await self._task_runs.set_worktree(run, str(handle.path), handle.branch)

        # 2-4) CODING → TESTING → REVIEWING, iterating on feedback.
        body = await self._notion.fetch_page_body(task.notion_page_id)
        max_iters = settings.max_iterations
        feedback: str | None = None  # set once a pass needs fixes; drives the next coder pass
        gate_suite: GateSuite | None = None
        verdict: ReviewVerdict | None = None
        changed: list[str] = []
        for attempt in range(1, max_iters + 1):
            # CODING — first pass implements; later passes fix prior feedback
            await self._set_status(run, TaskRunStatus.CODING)
            if feedback is None:
                prompt = build_coder_prompt(task, body=body)
            else:
                prompt = build_fix_prompt(task, feedback, body=body)
            stream: _CodingStream | None = None
            coder_prefix = "🧑‍💻" if attempt == 1 else f"🧑‍💻 #{attempt}"
            if settings.stream_coding_to_telegram:
                stream = _CodingStream(
                    self._tg, task, settings.stream_min_interval_seconds, prefix=coder_prefix
                )
                await stream.start()
            else:
                stage = "Claude пишет код" if attempt == 1 else f"Claude правит (попытка {attempt})"
                await self._notify(task, run, stage)
            coding = await self._claude.run(
                ClaudeRunRequest(
                    cwd=handle.path,
                    prompt=prompt,
                    model=settings.coder_model,
                    timeout_seconds=1800,
                ),
                on_event=stream,
            )
            if stream is not None:
                ok = "✅ кодинг завершён" if coding.success else "⚠️ кодинг не удался"
                await stream.finalize(ok)
            await self._task_runs.append_event(
                run,
                {
                    "stage": "coding",
                    "attempt": attempt,
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

            # Drop unintended uv.lock churn: tooling can rewrite the lockfile even
            # when no dependency changed. If pyproject.toml is untouched, restore
            # the lockfile to base so it never pollutes the MR.
            await self._drop_lockfile_noise(handle, run)

            # TESTING — gates, scoped to the files this run actually changed
            await self._set_status(run, TaskRunStatus.TESTING)
            changed = await self._worktree.changed_files(handle)
            await self._notify(task, run, "линтер и тесты по изменённым файлам")
            gate_suite = await self._gates.run(handle.path, changed)
            await self._task_runs.append_event(
                run,
                {
                    "stage": "gates",
                    "attempt": attempt,
                    "passed": gate_suite.all_passed,
                    "gates_run": [r.name for r in gate_suite.results],
                    "failures": [r.name for r in gate_suite.failures],
                },
            )
            if not gate_suite.all_passed:
                if attempt < max_iters:
                    feedback = gate_suite.feedback_for_claude()
                    logger.info(
                        "execution.iterate reason=gates attempt={a} next={n} failures={f}",
                        a=attempt,
                        n=attempt + 1,
                        f=[r.name for r in gate_suite.failures],
                    )
                    await self._notify(
                        task, run, f"gates провалены — итерация {attempt + 1}/{max_iters}"
                    )
                    continue
                raise AgentError(
                    f"quality gates failed after {max_iters} iteration(s)",
                    details={"failures": [r.name for r in gate_suite.failures]},
                )

            # REVIEWING — reviewer agent
            await self._set_status(run, TaskRunStatus.REVIEWING)
            diff = await self._worktree.diff_against_base(handle)
            review_stream: _CodingStream | None = None
            review_prefix = "🔍" if attempt == 1 else f"🔍 #{attempt}"
            if settings.stream_coding_to_telegram:
                review_stream = _CodingStream(
                    self._tg, task, settings.stream_min_interval_seconds, prefix=review_prefix
                )
                await review_stream.start()
            else:
                await self._notify(task, run, "reviewer проверяет diff")
            verdict = await self._reviewer.review(
                worktree_path=handle.path,
                diff_text=diff,
                task_brief=task.title,
                on_event=review_stream,
            )
            if review_stream is not None:
                await review_stream.finalize(f"🔍 вердикт: {verdict.verdict}")
            run.iterations_review += 1
            await run.save(update_fields=["iterations_review", "updated_at"])
            await self._task_runs.append_event(
                run,
                {
                    "stage": "review",
                    "attempt": attempt,
                    "verdict": verdict.verdict,
                    "summary": verdict.summary,
                    "blocking": len(verdict.blocking_issues),
                },
            )
            if not verdict.is_approved:
                if attempt < max_iters:
                    feedback = verdict.feedback_for_claude()
                    logger.info(
                        "execution.iterate reason=review attempt={a} next={n} verdict={v}",
                        a=attempt,
                        n=attempt + 1,
                        v=verdict.verdict,
                    )
                    await self._notify(
                        task,
                        run,
                        f"reviewer запросил правки — итерация {attempt + 1}/{max_iters}",
                    )
                    continue
                raise AgentError(
                    f"reviewer requested changes after {max_iters} iteration(s)",
                    details={"summary": verdict.summary},
                )

            # Gates passed and reviewer approved — done iterating.
            break

        assert gate_suite is not None and verdict is not None  # loop runs ≥1 pass

        if self._dry_run:
            logger.info(
                "execution.dry_run_done page={p} verdict={v} gates_passed={g}",
                p=task.notion_page_id,
                v=verdict.verdict,
                g=gate_suite.all_passed,
            )
            await self._notify(task, run, "dry-run: дошли до review, push пропущен")
            await self._task_runs.mark_done(run)
            await self._worktree.remove(handle.slug)
            return

        # 5) PUSHING — push branch + open MR
        await self._set_status(run, TaskRunStatus.PUSHING)
        await self._notify(task, run, "пушу ветку и открываю MR")
        await self._worktree.push(handle)
        mr = await self._gitlab.create_merge_request(
            MRCreateRequest(
                source_branch=handle.branch,
                target_branch=self._repo.default_branch,
                title=f"[ai-agent] {task.title}",
                description=build_mr_description(task, verdict, gate_suite, task.url, changed),
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
