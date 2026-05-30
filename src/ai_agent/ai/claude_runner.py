"""Async subprocess wrapper around the Claude Code CLI.

Knows nothing about tasks, git, or the orchestrator. Given a working directory
and a prompt, runs `claude --print --output-format stream-json` and returns a
structured result with the full event log.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import monotonic, time
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict

PermissionMode = Literal["acceptEdits", "auto", "bypassPermissions", "default", "plan"]

DEFAULT_ALLOWED_TOOLS: tuple[str, ...] = (
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "MultiEdit",
    "Bash(ruff:*)",
    "Bash(mypy:*)",
    "Bash(pytest:*)",
    "Bash(uv:*)",
    "Bash(python:*)",
    "Bash(python3:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git status:*)",
    "Bash(git add:*)",
    "Bash(git commit:*)",
    "Bash(ls:*)",
    "Bash(cat:*)",
    "Bash(find:*)",
    "Bash(grep:*)",
)
DEFAULT_DISALLOWED_TOOLS: tuple[str, ...] = (
    "Bash(git push:*)",
    "Bash(rm -rf:*)",
    "Bash(sudo:*)",
)


class ClaudeRunRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    cwd: Path
    prompt: str
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    model: str | None = None  # alias ('sonnet'/'opus') or full id; None = CLI default
    allowed_tools: tuple[str, ...] = DEFAULT_ALLOWED_TOOLS
    disallowed_tools: tuple[str, ...] = DEFAULT_DISALLOWED_TOOLS
    permission_mode: PermissionMode = "acceptEdits"
    timeout_seconds: int = 1800
    # If no stream event arrives for this long, assume the CLI is stalled
    # (silently waiting on a rate-limit window) and bail out.
    inactivity_timeout_seconds: int = 300
    # If a blocking rate-limit event says the window resets more than this many
    # seconds away, bail immediately instead of waiting.
    rate_limit_wait_threshold_seconds: int = 60
    extra_args: tuple[str, ...] = ()


class ClaudeRunResult(BaseModel):
    success: bool
    final_message: str | None
    error: str | None
    duration_seconds: float
    exit_code: int | None
    events: list[dict[str, Any]]
    stderr: str
    rate_limited: bool = False
    rate_limit_resets_at: int | None = None  # unix epoch seconds

    @property
    def event_count(self) -> int:
        return len(self.events)


def _rate_limit_blocking(info: dict[str, Any]) -> bool:
    """A rate_limit_event blocks progress when the window is no longer 'allowed'."""

    return info.get("status") not in (None, "allowed")


EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class ClaudeRunner:
    """Run the Claude Code CLI as an async subprocess and capture stream-json output."""

    def __init__(self, claude_executable: str = "claude") -> None:
        self._claude = claude_executable

    async def run(
        self,
        request: ClaudeRunRequest,
        on_event: EventCallback | None = None,
    ) -> ClaudeRunResult:
        if not request.cwd.is_dir():
            raise ValueError(f"cwd does not exist: {request.cwd}")

        argv = self._build_argv(request)
        logger.info(
            "claude.run.start cwd={c} argc={n} tools_allow={a} tools_deny={d}",
            c=str(request.cwd),
            n=len(argv),
            a=len(request.allowed_tools),
            d=len(request.disallowed_tools),
        )

        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(request.cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None
        proc.stdin.write(request.prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        events: list[dict[str, Any]] = []
        result_event: dict[str, Any] | None = None
        last_rate_limit: dict[str, Any] | None = None
        started = monotonic()
        last_activity = monotonic()
        abort_reason: str | None = None

        async def read_stdout() -> None:
            nonlocal result_event, last_rate_limit, last_activity, abort_reason
            assert proc.stdout is not None
            async for raw in proc.stdout:
                last_activity = monotonic()
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("claude.run.bad_line line={l!r}", l=line[:200])
                    continue
                events.append(event)
                etype = event.get("type")
                if etype == "result":
                    result_event = event
                elif etype == "rate_limit_event":
                    info = event.get("rate_limit_info", {})
                    last_rate_limit = info
                    if _rate_limit_blocking(info):
                        resets_at = info.get("resetsAt")
                        wait = (resets_at - time()) if resets_at else None
                        if wait is None or wait > request.rate_limit_wait_threshold_seconds:
                            abort_reason = "rate_limited"
                            logger.warning(
                                "claude.run.rate_limited status={s} resets_at={r}",
                                s=info.get("status"),
                                r=resets_at,
                            )
                            return
                if on_event is not None:
                    try:
                        await on_event(event)
                    except Exception:
                        logger.exception("claude.run.on_event_failed")

        async def watchdog() -> None:
            nonlocal abort_reason
            while True:
                await asyncio.sleep(5)
                if proc.returncode is not None:
                    return
                idle = monotonic() - last_activity
                if idle > request.inactivity_timeout_seconds:
                    abort_reason = "stalled"
                    logger.warning(
                        "claude.run.stalled idle={i}s cwd={c}",
                        i=round(idle),
                        c=str(request.cwd),
                    )
                    return

        timed_out = False
        reader = asyncio.create_task(read_stdout())
        guard = asyncio.create_task(watchdog())
        waiter = asyncio.create_task(proc.wait())
        try:
            done, _pending = await asyncio.wait(
                {reader, guard, waiter},
                timeout=request.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                timed_out = True
                logger.warning("claude.run.timeout cwd={c}", c=str(request.cwd))
        finally:
            for t in (reader, guard, waiter):
                t.cancel()
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

        duration = monotonic() - started
        stderr_text = ""
        if proc.stderr is not None:
            try:
                stderr_bytes = await asyncio.wait_for(proc.stderr.read(), timeout=2)
                stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            except TimeoutError:
                pass

        resets_at = last_rate_limit.get("resetsAt") if last_rate_limit else None

        if abort_reason == "rate_limited":
            return ClaudeRunResult(
                success=False,
                final_message=None,
                error="rate limited — window exhausted",
                duration_seconds=duration,
                exit_code=None,
                events=events,
                stderr=stderr_text,
                rate_limited=True,
                rate_limit_resets_at=resets_at,
            )

        if abort_reason == "stalled":
            blocking = last_rate_limit is not None and _rate_limit_blocking(last_rate_limit)
            return ClaudeRunResult(
                success=False,
                final_message=None,
                error=f"stalled — no output for {request.inactivity_timeout_seconds}s",
                duration_seconds=duration,
                exit_code=None,
                events=events,
                stderr=stderr_text,
                rate_limited=blocking,
                rate_limit_resets_at=resets_at if blocking else None,
            )

        if timed_out:
            return ClaudeRunResult(
                success=False,
                final_message=None,
                error=f"timeout after {request.timeout_seconds}s",
                duration_seconds=duration,
                exit_code=None,
                events=events,
                stderr=stderr_text,
            )

        if proc.returncode != 0:
            return ClaudeRunResult(
                success=False,
                final_message=None,
                error=f"exit code {proc.returncode}",
                duration_seconds=duration,
                exit_code=proc.returncode,
                events=events,
                stderr=stderr_text,
            )

        final_message = _extract_final_message(result_event)
        is_success = result_event is not None and result_event.get("subtype") == "success"
        return ClaudeRunResult(
            success=is_success,
            final_message=final_message,
            error=None if is_success else "no success result event",
            duration_seconds=duration,
            exit_code=proc.returncode,
            events=events,
            stderr=stderr_text,
        )

    def _build_argv(self, request: ClaudeRunRequest) -> list[str]:
        # The CLI accepts a positional <prompt> after --print. To avoid the
        # variadic --allowed-tools/--disallowed-tools eating the prompt, we
        # pass the prompt via stdin instead and use --input-format text.
        argv: list[str] = [
            self._claude,
            "--print",
            "--input-format",
            "text",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            request.permission_mode,
        ]
        if request.model:
            argv += ["--model", request.model]
        if request.allowed_tools:
            argv += ["--allowed-tools", ",".join(request.allowed_tools)]
        if request.disallowed_tools:
            argv += ["--disallowed-tools", ",".join(request.disallowed_tools)]
        if request.system_prompt:
            argv += ["--system-prompt", request.system_prompt]
        if request.append_system_prompt:
            argv += ["--append-system-prompt", request.append_system_prompt]
        argv += list(request.extra_args)
        return argv


def _extract_final_message(result_event: dict[str, Any] | None) -> str | None:
    if result_event is None:
        return None
    value = result_event.get("result")
    return value if isinstance(value, str) else None
