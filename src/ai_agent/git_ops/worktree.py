from __future__ import annotations

import hashlib
import re
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, ConfigDict

from ai_agent.errors import GitError
from ai_agent.git_ops._subprocess import run_git
from ai_agent.git_ops.repo import RepoManager

_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_changed_lines(diff_text: str) -> dict[str, set[int]]:
    """Parse `git diff --unified=0` into {repo-relative path: {new line numbers}}.

    Only lines added/modified on the NEW side are recorded (pure deletions add
    no new lines). A file with only deletions does not appear in the map.
    """

    result: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current = None
            else:
                current = target[2:] if target.startswith("b/") else target
            continue
        if current is None:
            continue
        m = _HUNK_RE.match(line)
        if m:
            start = int(m.group(1))
            count = 1 if m.group(2) is None else int(m.group(2))
            if count > 0:
                result.setdefault(current, set()).update(range(start, start + count))
    return result


def make_branch_slug(task_id: int | None, title: str, max_len: int = 60) -> str:
    """Turn a task title into a safe, unique, valid git ref slug.

    Handles titles that sanitise to nothing (e.g. all-Cyrillic/emoji) by falling
    back to a stable hash so distinct tasks never collide on the slug 'task', and
    strips the components git forbids in refnames ('..', leading/trailing '.'/'-',
    a trailing '.lock').
    """

    prefix = f"T-{task_id}-" if task_id else ""
    sanitized = _SLUG_RE.sub("-", title.lower())
    sanitized = re.sub(r"-+", "-", sanitized).replace("..", "-")
    body = sanitized[: max_len - len(prefix)].strip("-._")
    if body.endswith(".lock"):
        body = body[: -len(".lock")].strip("-._")
    if not body:
        # No ASCII-safe content — derive a stable per-title suffix so distinct
        # tasks get distinct branches/worktrees instead of all collapsing to one.
        digest = hashlib.sha1(title.encode("utf-8"), usedforsecurity=False).hexdigest()
        body = f"task-{digest[:8]}"
    return f"{prefix}{body}".strip("-")


class WorktreeHandle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    slug: str
    branch: str
    path: Path


class WorktreeManager:
    """Creates and tears down per-task git worktrees on top of a base clone."""

    def __init__(
        self,
        repo: RepoManager,
        worktrees_root: Path,
        author_name: str = "",
        author_email: str = "",
    ) -> None:
        self._repo = repo
        self._root = worktrees_root
        self._author_name = author_name
        self._author_email = author_email

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
        # Attribute the agent's commits to the human owner (GitLab links commits
        # to accounts by email), overriding any default identity.
        if self._author_name:
            await run_git("config", "user.name", self._author_name, cwd=path)
        if self._author_email:
            await run_git("config", "user.email", self._author_email, cwd=path)
        return WorktreeHandle(slug=slug, branch=branch, path=path)

    async def remove(self, slug: str, *, delete_branch: bool = True) -> None:
        path = self._root / slug
        branch = f"ai-agent/{slug}"
        logger.info("worktree.remove slug={s}", s=slug)
        try:
            # Two --force flags also drop a worktree left 'locked' (e.g. a run
            # killed mid 'worktree add', lock reason 'initializing').
            await run_git(
                "worktree",
                "remove",
                "--force",
                "--force",
                str(path),
                cwd=self._repo.path,
            )
        except GitError as e:
            logger.warning("worktree.remove_failed slug={s} detail={d}", s=slug, d=e.details)
        # Always prune the registry in case the dir was already gone.
        try:
            await run_git("worktree", "prune", cwd=self._repo.path)
        except GitError:
            pass
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

    async def changed_files(self, handle: WorktreeHandle) -> list[str]:
        """Repo-relative paths changed on this branch vs the base, committed."""

        out = await run_git(
            "diff",
            "--name-only",
            f"origin/{self._repo.default_branch}...HEAD",
            cwd=handle.path,
        )
        return [line.strip() for line in out.splitlines() if line.strip()]

    async def changed_line_map(self, handle: WorktreeHandle) -> dict[str, set[int]]:
        """Per changed file, the set of NEW-file line numbers this branch adds.

        Used to scope quality gates to the lines the change is responsible for,
        so a legacy file's pre-existing lint/type errors don't fail the run.
        """

        out = await run_git(
            "diff",
            "--unified=0",
            f"origin/{self._repo.default_branch}...HEAD",
            cwd=handle.path,
        )
        return parse_changed_lines(out)

    async def revert_file_to_base(
        self, handle: WorktreeHandle, rel_path: str, *, message: str
    ) -> bool:
        """Restore one file to its base version and commit, if it differs.

        Used to drop unintended churn (e.g. a tool regenerating uv.lock) from a
        branch. Returns True if a revert commit was made.
        """

        base = f"origin/{self._repo.default_branch}"
        await run_git("checkout", base, "--", rel_path, cwd=handle.path)
        status = await run_git("status", "--porcelain", "--", rel_path, cwd=handle.path)
        if not status.strip():
            return False
        await run_git("commit", "-m", message, "--", rel_path, cwd=handle.path)
        logger.info("worktree.reverted slug={s} path={p}", s=handle.slug, p=rel_path)
        return True

    async def head_commit(self, handle: WorktreeHandle) -> str:
        return (await run_git("rev-parse", "HEAD", cwd=handle.path)).strip()
