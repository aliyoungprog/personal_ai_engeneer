from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from loguru import logger

from ai_agent.git_ops._subprocess import run_git


class RepoManager:
    """Owns a base clone of one project. Used as the parent for git worktrees."""

    def __init__(
        self,
        base_path: Path,
        https_url: str,
        token: str,
        default_branch: str = "test",
    ) -> None:
        self._base_path = base_path
        self._https_url = https_url
        self._token = token
        self._default_branch = default_branch

    @property
    def path(self) -> Path:
        return self._base_path

    @property
    def default_branch(self) -> str:
        return self._default_branch

    def _authed_url(self) -> str:
        parsed = urlparse(self._https_url)
        return f"{parsed.scheme}://oauth2:{self._token}@{parsed.netloc}{parsed.path}"

    async def ensure_cloned(self) -> None:
        if (self._base_path / ".git").exists():
            logger.info("repo.fetch path={p}", p=str(self._base_path))
            # Keep origin's embedded credential in sync with the configured
            # token (it may have changed since the initial clone).
            await run_git("remote", "set-url", "origin", self._authed_url(), cwd=self._base_path)
            await run_git("fetch", "--prune", "origin", cwd=self._base_path)
            return
        logger.info("repo.clone path={p}", p=str(self._base_path))
        self._base_path.parent.mkdir(parents=True, exist_ok=True)
        await run_git(
            "clone",
            "--branch",
            self._default_branch,
            self._authed_url(),
            str(self._base_path),
            timeout_seconds=600,
        )

    async def fetch(self) -> None:
        await run_git("fetch", "--prune", "origin", cwd=self._base_path)
