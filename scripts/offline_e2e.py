"""Offline end-to-end probe of the coding pipeline against the REAL repo.

Runs: clone -> worktree -> Claude codes a safe change -> diff -> reviewer.
STOPS before push / MR creation, then removes the worktree. No Telegram,
no GitLab writes — safe to run against the corporate repo.

Usage (inside the container):
    docker exec ai-agent python scripts/offline_e2e.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from ai_agent.ai import ClaudeRunner, ClaudeRunRequest, ReviewerAgent
from ai_agent.git_ops import RepoManager, WorktreeManager
from ai_agent.git_ops.worktree import make_branch_slug
from ai_agent.settings import settings

SAFE_TITLE = "Add docs/AI_AGENT_SMOKE.md marker file"
SAFE_BODY = """\
## Description
Create a new Markdown file at `docs/AI_AGENT_SMOKE.md` with this content:

- An H1 title: "AI Agent Smoke Test"
- One sentence saying this file was created by an automated agent to verify
  the end-to-end pipeline, and can be safely deleted.

Do not modify any other file. Keep it minimal.
"""


async def main() -> None:
    repo = RepoManager(
        base_path=Path("/app/repos/ai-agents-platform"),
        https_url=f"{settings.gitlab_url}/{settings.gitlab_project_path}.git",
        token=settings.gitlab_token,
        default_branch="test",
    )
    worktree = WorktreeManager(repo, Path("/app/worktrees"))
    runner = ClaudeRunner()
    reviewer = ReviewerAgent(runner)

    logger.info("e2e.clone")
    await repo.ensure_cloned()

    slug = make_branch_slug(9999, SAFE_TITLE)
    logger.info("e2e.worktree slug={s}", s=slug)
    handle = await worktree.create(slug)

    try:
        prompt = (
            f"You are implementing a task.\n\n## Title\n{SAFE_TITLE}\n\n{SAFE_BODY}\n\n"
            "## Working agreement\n"
            "- Read CLAUDE.md if present.\n"
            "- Stay strictly within scope; create only the requested file.\n"
            "- Commit with a concise conventional-commit message.\n"
            "- Reply with a one-line summary when done."
        )
        logger.info("e2e.coding.start")
        coding = await runner.run(
            ClaudeRunRequest(cwd=handle.path, prompt=prompt, timeout_seconds=600),
        )
        logger.info(
            "e2e.coding.done success={s} dur={d}s final={f!r}",
            s=coding.success,
            d=round(coding.duration_seconds, 1),
            f=(coding.final_message or "")[:200],
        )

        diff = await worktree.diff_against_base(handle)
        print("\n===== DIFF =====")
        print(diff if diff.strip() else "(empty diff)")

        if not diff.strip():
            logger.warning("e2e.no_diff — coder produced no committed changes")
            return

        logger.info("e2e.review.start")
        verdict = await reviewer.review(
            worktree_path=handle.path,
            diff_text=diff,
            task_brief=SAFE_TITLE,
        )
        print("\n===== REVIEW =====")
        print("verdict :", verdict.verdict)
        print("summary :", verdict.summary)
        for f in verdict.blocking_issues:
            print(f"  BLOCK {f.file}:{f.lines} — {f.issue}")
        for f in verdict.non_blocking:
            print(f"  NOTE  {f.file} — {f.issue}")
    finally:
        logger.info("e2e.cleanup slug={s}", s=slug)
        await worktree.remove(slug)


if __name__ == "__main__":
    asyncio.run(main())
