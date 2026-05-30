from __future__ import annotations

import re
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, ConfigDict

from ai_agent.errors import GitError
from ai_agent.git_ops._subprocess import run_git
from ai_agent.git_ops.repo import RepoManager

_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def make_branch_slug(task_id: int | None, title: str, max_len: int = 60) -> str:
    """Turn a task title into a safe git ref slug like 'T-456-add-metrics'."""

    prefix = f"T-{task_id}-" if task_id else ""
    sanitized = _SLUG_RE.sub("-", title.lower()).strip("-")
    sanitized = re.sub(r"-+", "-", sanitized)
    body = sanitized[: max_len - len(prefix)] or "task"
    return f"{prefix}{body}".strip("-")


class WorktreeHandle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    slug: str
    branch: str
    path: Path


class WorktreeManager:
    """Creates and tears down per-task git worktrees on top of a base clone."""

    def __init__(self, repo: RepoManager, worktrees_root: Path) -> None:
        self._repo = repo
        self._root = worktrees_root

    async def create(self, slug: str) -> WorktreeHandle:
        path = self._root / slug
        branch = f"ai-agent/{slug}"
        base_ref = f"origin/{self._repo.default_branch}"

        # A previous run for the same slug may have crashed before cleanup.
        # Remove any stale worktree + branch so a retry starts clean.
        if path.exists():
            logger.warning("worktree.stale_found slug={s} — removing before recreate", s=slug)
            await self.remove(slug)
        # Prune git's worktree registry in case the dir was deleted out-of-band,
        # then drop a leftover branch so 'worktree add -b' won't collide.
        await run_git("worktree", "prune", cwd=self._repo.path)
        try:
            await run_git("branch", "-D", branch, cwd=self._repo.path)
        except GitError:
            pass

        self._root.mkdir(parents=True, exist_ok=True)
        logger.info("worktree.create slug={s} branch={b}", s=slug, b=branch)
        await run_git(
            "worktree",
            "add",
            str(path),
            "-b",
            branch,
            base_ref,
            cwd=self._repo.path,
        )
        return WorktreeHandle(slug=slug, branch=branch, path=path)

    async def remove(self, slug: str, *, delete_branch: bool = True) -> None:
        path = self._root / slug
        branch = f"ai-agent/{slug}"
        logger.info("worktree.remove slug={s}", s=slug)
        try:
            await run_git(
                "worktree",
                "remove",
                "--force",
                str(path),
                cwd=self._repo.path,
            )
        except GitError as e:
            logger.warning("worktree.remove_failed slug={s} detail={d}", s=slug, d=e.details)
        if delete_branch:
            try:
                await run_git("branch", "-D", branch, cwd=self._repo.path)
            except GitError:
                pass

    async def push(self, handle: WorktreeHandle, *, force_with_lease: bool = False) -> None:
        """Push the worktree's branch to origin."""

        args = ["push", "-u"]
        if force_with_lease:
            args.append("--force-with-lease")
        args += ["origin", handle.branch]
        logger.info("worktree.push slug={s} branch={b}", s=handle.slug, b=handle.branch)
        await run_git(*args, cwd=handle.path)

    async def diff_against_base(self, handle: WorktreeHandle) -> str:
        return await run_git(
            "diff",
            f"origin/{self._repo.default_branch}...HEAD",
            cwd=handle.path,
        )

    async def head_commit(self, handle: WorktreeHandle) -> str:
        return (await run_git("rev-parse", "HEAD", cwd=handle.path)).strip()
