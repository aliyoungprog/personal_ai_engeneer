"""Live demo of the coding pipeline against the REAL repo.

Stages shown: clone -> Claude codes -> quality gates (ruff+pytest on the
changed files) -> diff -> reviewer. STOPS before push/MR. Cleans up the
worktree at the end. No GitLab writes, no Telegram.

Task is a self-contained Python module + test under scratch/ so the gate
stage has real code to lint and test, without touching production code.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from ai_agent.ai import ClaudeRunner, ClaudeRunRequest, ReviewerAgent
from ai_agent.gates import GateSpec, QualityGates
from ai_agent.git_ops import RepoManager, WorktreeManager
from ai_agent.git_ops.worktree import make_branch_slug
from ai_agent.settings import settings

TITLE = "Add scratch/agent_demo/temperature.py with celsius<->fahrenheit + tests"
BODY = """\
## Description
Create a small, self-contained module to demonstrate the pipeline:

1. `scratch/agent_demo/__init__.py` (empty).
2. `scratch/agent_demo/temperature.py` with two pure functions:
   - `c_to_f(celsius: float) -> float`  (celsius * 9/5 + 32)
   - `f_to_c(fahrenheit: float) -> float`  ((fahrenheit - 32) * 5/9)
   Both fully type-annotated with short docstrings.
3. `scratch/agent_demo/test_temperature.py` with pytest tests covering
   freezing point, boiling point, and a round-trip.

Keep everything inside scratch/agent_demo/. Do not modify any other file.
Make sure `ruff` is clean and tests pass.
"""

SECTION = "=" * 60


def banner(text: str) -> None:
    print(f"\n{SECTION}\n  {text}\n{SECTION}")


async def main() -> None:
    repo = RepoManager(
        base_path=Path("/app/repos/ai-agents-platform"),
        https_url=f"{settings.gitlab_url}/{settings.gitlab_project_path}.git",
        token=settings.gitlab_token,
        default_branch="test",
    )
    worktree = WorktreeManager(repo, Path("/app/worktrees"))
    runner = ClaudeRunner()
    reviewer = ReviewerAgent(runner, model=settings.reviewer_model)

    banner("STAGE 1/5  PREPARING — clone + worktree")
    await repo.ensure_cloned()
    slug = make_branch_slug(8888, "demo temperature module")
    handle = await worktree.create(slug)
    print(f"worktree: {handle.path}\nbranch:   {handle.branch}")

    try:
        banner("STAGE 2/5  CODING — Claude writes code")
        prompt = (
            f"You are implementing a task.\n\n## Title\n{TITLE}\n\n{BODY}\n\n"
            "## Working agreement\n"
            "- Read CLAUDE.md if present and follow conventions.\n"
            "- Stay strictly within scratch/agent_demo/.\n"
            "- Run `ruff check scratch/agent_demo/` before finishing.\n"
            "- Commit with a concise conventional-commit message.\n"
            "- Reply with a one-line summary when done."
        )
        coding = await runner.run(
            ClaudeRunRequest(
                cwd=handle.path,
                prompt=prompt,
                model=settings.coder_model,
                timeout_seconds=900,
            ),
        )
        print(f"success: {coding.success}  duration: {coding.duration_seconds:.1f}s")
        print(f"summary: {coding.final_message}")

        banner("STAGE 3/5  TESTING — quality gates on changed code")
        gates = QualityGates(
            gates=(
                GateSpec(
                    name="lint",
                    command=("ruff", "check", "scratch/agent_demo/"),
                ),
                GateSpec(
                    name="test",
                    command=("python", "-m", "pytest", "-q", "scratch/agent_demo/"),
                    timeout_seconds=300,
                ),
            )
        )
        suite = await gates.run(handle.path)
        for r in suite.results:
            mark = "PASS" if r.passed else "FAIL"
            print(f"  [{mark}] {r.name}  ({r.duration_seconds:.1f}s, exit={r.exit_code})")
            if not r.passed:
                print(r.stdout_tail[-500:])
        print(f"all_passed: {suite.all_passed}")

        banner("STAGE 4/5  DIFF")
        diff = await worktree.diff_against_base(handle)
        print(diff if diff.strip() else "(empty diff)")

        banner("STAGE 5/5  REVIEWING — reviewer agent verdict")
        verdict = await reviewer.review(
            worktree_path=handle.path,
            diff_text=diff,
            task_brief=TITLE,
        )
        print(f"verdict: {verdict.verdict}")
        print(f"summary: {verdict.summary}")
        for f in verdict.blocking_issues:
            print(f"  BLOCK {f.file}:{f.lines} — {f.issue}")
        for f in verdict.non_blocking:
            print(f"  NOTE  {f.file} — {f.issue}")

        banner("RESULT")
        gates_ok = suite.all_passed
        review_ok = verdict.is_approved
        print(f"gates passed : {gates_ok}")
        print(f"review passed: {review_ok}")
        if gates_ok and review_ok:
            print("\n=> In a live run this is where the agent would push the")
            print("   branch, open the MR, and send you the Approve button.")
        else:
            print("\n=> In a live run this would trigger an iteration (Phase 1.9b).")
    finally:
        banner("CLEANUP — removing worktree")
        await worktree.remove(slug)
        print("done")


if __name__ == "__main__":
    asyncio.run(main())
