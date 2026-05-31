"""Unit tests for the coder ↔ gates/reviewer iteration loop in TaskExecutionService.

The loop is the core of P0: when gates fail or the reviewer requests changes,
their feedback is fed back to the coder for a fresh fix pass, up to
`settings.max_iterations` passes; the run fails only after the last pass.

Every external dependency is mocked — we assert on how many coder/reviewer
passes happen and on the feedback threaded into each follow-up coder prompt.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_agent.ai import ReviewFinding, ReviewVerdict
from ai_agent.ai.claude_runner import ClaudeRunResult
from ai_agent.errors import AgentError
from ai_agent.gates import GateResult, GateSuite
from ai_agent.git_ops.worktree import WorktreeHandle
from ai_agent.services.task_execution_service import TaskExecutionService
from ai_agent.settings import settings


def _coding(success: bool = True) -> ClaudeRunResult:
    return ClaudeRunResult(
        success=success,
        final_message="done",
        error=None,
        duration_seconds=1.0,
        exit_code=0,
        events=[],
        stderr="",
    )


def _gates(passed: bool = True) -> GateSuite:
    results = (
        []
        if passed
        else [
            GateResult(
                name="lint",
                passed=False,
                duration_seconds=1.0,
                exit_code=1,
                stdout_tail="E501 line too long",
                stderr_tail="",
            )
        ]
    )
    return GateSuite(cwd=Path("/work/wt"), results=results)


def _approve() -> ReviewVerdict:
    return ReviewVerdict(verdict="approve", summary="looks good")


def _comment() -> ReviewVerdict:
    return ReviewVerdict(
        verdict="comment",
        summary="fine, minor nits",
        non_blocking=[ReviewFinding(file="app/foo.py", issue="consider a docstring")],
    )


def _request_changes() -> ReviewVerdict:
    return ReviewVerdict(
        verdict="request_changes",
        summary="hardcoded secret",
        blocking_issues=[ReviewFinding(file="env_example.env", issue="hardcoded API key")],
    )


@pytest.fixture
def task() -> Any:
    return SimpleNamespace(
        notion_page_id="page1234abcd",
        notion_task_id=493,
        title="M-4 Centrifugo defaults",
        url="https://notion.so/page1234abcd",
    )


@pytest.fixture
def run() -> Any:
    return SimpleNamespace(
        id="run-1",
        task_id="task-1",
        status="pending",
        iterations_code=0,
        iterations_review=0,
        branch_name="ai-agent/T-493",
        save=AsyncMock(),
    )


def _build_service(
    *,
    coder_results: list[ClaudeRunResult],
    gate_results: list[GateSuite],
    verdicts: list[ReviewVerdict],
    dry_run: bool = True,
) -> tuple[TaskExecutionService, MagicMock, AsyncMock]:
    claude = MagicMock()
    claude.run = AsyncMock(side_effect=coder_results)

    reviewer = MagicMock()
    reviewer.review = AsyncMock(side_effect=verdicts)

    gates = MagicMock()
    gates.run = AsyncMock(side_effect=gate_results)

    worktree = MagicMock()
    worktree.create = AsyncMock(
        return_value=WorktreeHandle(slug="T-493", branch="ai-agent/T-493", path=Path("/work/wt"))
    )
    # changed_files is used both by _drop_lockfile_noise and the gates scope —
    # no "uv.lock" entry, so the lockfile-revert path is skipped.
    worktree.changed_files = AsyncMock(return_value=["app/foo.py"])
    worktree.diff_against_base = AsyncMock(return_value="diff --git a/app/foo.py")
    worktree.revert_file_to_base = AsyncMock(return_value=False)
    worktree.push = AsyncMock()
    worktree.remove = AsyncMock()

    repo = MagicMock()
    repo.ensure_cloned = AsyncMock()
    repo.default_branch = "test"

    task_runs = MagicMock()
    task_runs.set_worktree = AsyncMock()
    task_runs.set_status = AsyncMock()
    task_runs.append_event = AsyncMock()
    task_runs.mark_done = AsyncMock()
    task_runs.set_mr = AsyncMock()

    notion = MagicMock()
    notion.fetch_page_body = AsyncMock(return_value="task body")

    tg = MagicMock()
    tg.send_execution_progress = AsyncMock()
    tg.send_mr_ready = AsyncMock()

    service = TaskExecutionService(
        task_runs_repository=task_runs,
        tasks_repository=MagicMock(),
        claude_runner=claude,
        reviewer=reviewer,
        quality_gates=gates,
        repo_manager=repo,
        worktree_manager=worktree,
        gitlab_client=MagicMock(),
        telegram_client=tg,
        notion_client=notion,
        dry_run=dry_run,
    )
    return service, claude, reviewer.review


@pytest.fixture(autouse=True)
def _loop_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Disable Telegram streaming so the loop uses the plain _notify path, and
    # pin a small iteration budget for deterministic assertions.
    monkeypatch.setattr(settings, "stream_coding_to_telegram", False)
    monkeypatch.setattr(settings, "max_iterations", 3)


def _coder_prompts(claude: MagicMock) -> list[str]:
    return [call.args[0].prompt for call in claude.run.call_args_list]


async def test_single_pass_when_first_attempt_clean(
    task: Any, run: Any
) -> None:
    service, claude, review = _build_service(
        coder_results=[_coding()],
        gate_results=[_gates(passed=True)],
        verdicts=[_approve()],
    )

    await service._execute(run, task)

    assert claude.run.call_count == 1
    assert review.call_count == 1
    assert run.iterations_code == 1
    assert run.iterations_review == 1


async def test_comment_verdict_drives_an_iteration(task: Any, run: Any) -> None:
    # "comment" is actionable: the engineer must address the reviewer's items,
    # so it drives another coder pass (only "approve" ends the loop). Here the
    # second pass then earns an approve.
    service, claude, review = _build_service(
        coder_results=[_coding(), _coding()],
        gate_results=[_gates(passed=True), _gates(passed=True)],
        verdicts=[_comment(), _approve()],
    )

    await service._execute(run, task)

    assert claude.run.call_count == 2
    assert review.call_count == 2
    prompts = _coder_prompts(claude)
    # The comment's non-blocking item is threaded into the follow-up coder prompt.
    assert "consider a docstring" in prompts[1]


async def test_reviewer_feedback_drives_a_second_coder_pass(
    task: Any, run: Any
) -> None:
    service, claude, review = _build_service(
        coder_results=[_coding(), _coding()],
        gate_results=[_gates(passed=True), _gates(passed=True)],
        verdicts=[_request_changes(), _approve()],
    )

    await service._execute(run, task)

    assert claude.run.call_count == 2
    assert review.call_count == 2
    prompts = _coder_prompts(claude)
    # First pass is the plain implementation prompt; the second is a fix prompt
    # that carries the reviewer's blocking issue back to the coder.
    assert "Feedback to address" not in prompts[0]
    assert "Feedback to address" in prompts[1]
    assert "hardcoded API key" in prompts[1]


async def test_gate_failure_drives_a_second_coder_pass(
    task: Any, run: Any
) -> None:
    service, claude, review = _build_service(
        coder_results=[_coding(), _coding()],
        gate_results=[_gates(passed=False), _gates(passed=True)],
        verdicts=[_approve()],
    )

    await service._execute(run, task)

    assert claude.run.call_count == 2
    # The reviewer only runs once — after the gates finally pass.
    assert review.call_count == 1
    prompts = _coder_prompts(claude)
    assert "E501 line too long" in prompts[1]


async def test_fails_after_max_iterations_when_reviewer_never_approves(
    task: Any, run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "max_iterations", 2)
    service, claude, review = _build_service(
        coder_results=[_coding(), _coding()],
        gate_results=[_gates(passed=True), _gates(passed=True)],
        verdicts=[_request_changes(), _request_changes()],
    )

    with pytest.raises(AgentError, match="reviewer requested changes after 2 iteration"):
        await service._execute(run, task)

    assert claude.run.call_count == 2
    assert review.call_count == 2
