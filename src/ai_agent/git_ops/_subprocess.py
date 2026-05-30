from __future__ import annotations

import asyncio
import os
from pathlib import Path

from loguru import logger

from ai_agent.errors import GitError


async def run_git(
    *args: str,
    cwd: Path | None = None,
    timeout_seconds: int = 300,
    extra_env: dict[str, str] | None = None,
) -> str:
    """Run a git subprocess and return stdout. Raise GitError on non-zero exit."""

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if extra_env:
        env.update(extra_env)

    logger.debug("git.run args={a} cwd={c}", a=args, c=str(cwd) if cwd else None)

    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise GitError(
            "git timeout",
            details={"args": args, "timeout_seconds": timeout_seconds},
        ) from e

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise GitError(
            "git exited non-zero",
            details={
                "args": args,
                "exit_code": proc.returncode,
                "stderr": stderr.strip()[:1000],
            },
        )
    return stdout
