"""Async subprocess wrapper around the Claude Code CLI.

Knows nothing about tasks, git, or the orchestrator. Given a working directory
and a prompt, runs `claude --print --output-format stream-json` and returns a
structured result with the full event log.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import tempfile
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import monotonic
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


def _parse_events(
    stdout_bytes: bytes,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    """Parse stream-json stdout into (events, last result event, last rate-limit info)."""

    events: list[dict[str, Any]] = []
    result_event: dict[str, Any] | None = None
    last_rate_limit: dict[str, Any] | None = None
    for raw in stdout_bytes.decode("utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(event)
        etype = event.get("type")
        if etype == "result":
            result_event = event
        elif etype == "rate_limit_event":
            last_rate_limit = event.get("rate_limit_info", {})
    return events, result_event, last_rate_limit


EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
# Called (from the worker thread) with the live Popen right after spawn, so the
# orchestrator can kill it to abort an in-flight run. Called with None on exit.
SpawnCallback = Callable[[subprocess.Popen[bytes] | None], None]


async def _relay(callback: EventCallback, event: dict[str, Any]) -> None:
    """Wrap an event callback in a coroutine for run_coroutine_threadsafe."""

    await callback(event)


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL the whole process group so claude's child git/node/pytest also die.

    proc is launched with start_new_session=True, so it leads its own group;
    killing the group reaps grandchildren that a plain proc.kill() would orphan.
    """

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


class ClaudeRunner:
    """Run the Claude Code CLI as an async subprocess and capture stream-json output."""

    def __init__(self, claude_executable: str = "claude") -> None:
        self._claude = claude_executable

    async def run(
        self,
        request: ClaudeRunRequest,
        on_event: EventCallback | None = None,
        on_spawn: SpawnCallback | None = None,
    ) -> ClaudeRunResult:
        if not request.cwd.is_dir():
            raise ValueError(f"cwd does not exist: {request.cwd}")

        argv = self._build_argv(request)
        logger.info(
            "claude.run.start cwd={c} argc={n} tools_allow={a} tools_deny={d} stream={s}",
            c=str(request.cwd),
            n=len(argv),
            a=len(request.allowed_tools),
            d=len(request.disallowed_tools),
            s=on_event is not None,
        )
        # NOTE: claude is spawned via a SYNCHRONOUS subprocess inside a worker
        # thread, NOT asyncio.create_subprocess_exec. On colima (vz, macOS) the
        # asyncio subprocess transport reliably SIGKILLs the child at ~20-30s;
        # a plain subprocess survives.
        #
        # When on_event is provided we read stdout line-by-line (each line is a
        # stream-json event) and bridge each event back onto the running event
        # loop via run_coroutine_threadsafe, so the orchestrator can stream the
        # coder's activity to Telegram in real time. Without on_event we use the
        # simpler buffered path that parses everything after completion.
        if on_event is None:
            return await asyncio.to_thread(self._run_blocking, argv, request)
        loop = asyncio.get_running_loop()
        return await asyncio.to_thread(
            self._run_streaming, argv, request, on_event, loop, on_spawn
        )

    def _run_blocking(self, argv: list[str], request: ClaudeRunRequest) -> ClaudeRunResult:
        # subprocess.run (via communicate) reads stdout+stderr concurrently and
        # survives on colima where incremental Popen reads / asyncio transport
        # get the node child SIGKILL'd. Events are parsed after completion.
        started = monotonic()
        timed_out = False
        stdout_bytes = b""
        stderr_bytes = b""
        try:
            completed = subprocess.run(  # noqa: S603 — argv built internally
                argv,
                cwd=str(request.cwd),
                input=request.prompt.encode("utf-8"),
                capture_output=True,
                timeout=request.timeout_seconds,
                check=False,
            )
            stdout_bytes = completed.stdout
            stderr_bytes = completed.stderr
            exit_code: int | None = completed.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            exit_code = None
            stdout_bytes = e.stdout or b""
            stderr_bytes = e.stderr or b""
            logger.warning("claude.run.timeout cwd={c}", c=str(request.cwd))

        duration = monotonic() - started
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        events, result_event, last_rate_limit = _parse_events(stdout_bytes)
        return self._finalize(
            request, events, result_event, last_rate_limit, duration, exit_code, stderr_text,
            timed_out=timed_out,
        )

    def _run_streaming(
        self,
        argv: list[str],
        request: ClaudeRunRequest,
        on_event: EventCallback,
        loop: asyncio.AbstractEventLoop,
        on_spawn: SpawnCallback | None = None,
    ) -> ClaudeRunResult:
        # Like _run_blocking, but reads stdout incrementally so each stream-json
        # event can be forwarded to on_event (which runs on `loop`) as it lands.
        # stderr is drained to a temp file to avoid a full-pipe deadlock, and a
        # watchdog timer enforces the hard timeout (iterating proc.stdout has no
        # native timeout).
        started = monotonic()
        events: list[dict[str, Any]] = []
        result_event: dict[str, Any] | None = None
        last_rate_limit: dict[str, Any] | None = None
        timed_out = threading.Event()
        stalled = threading.Event()

        with tempfile.TemporaryFile() as stderr_f:
            proc = subprocess.Popen(  # noqa: S603 — argv built internally
                argv,
                cwd=str(request.cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_f,
                start_new_session=True,
            )
            if on_spawn is not None:
                on_spawn(proc)

            def _on_hard_timeout() -> None:
                timed_out.set()
                logger.warning("claude.run.timeout cwd={c}", c=str(request.cwd))
                _kill_process_group(proc)

            def _on_inactivity() -> None:
                stalled.set()
                logger.warning(
                    "claude.run.stalled cwd={c} idle_s={s}",
                    c=str(request.cwd),
                    s=request.inactivity_timeout_seconds,
                )
                _kill_process_group(proc)

            watchdog = threading.Timer(request.timeout_seconds, _on_hard_timeout)
            watchdog.start()
            # Inactivity watchdog: reset on every event. If no stream-json event
            # arrives for inactivity_timeout_seconds the CLI is assumed wedged
            # (e.g. silently waiting on a rate-limit window) and is killed,
            # instead of blocking the whole queue for the full hard timeout.
            inactivity = threading.Timer(request.inactivity_timeout_seconds, _on_inactivity)
            inactivity.start()
            try:
                if proc.stdin is not None:
                    proc.stdin.write(request.prompt.encode("utf-8"))
                    proc.stdin.close()
                assert proc.stdout is not None
                # readline() (not `for line in stdout`) delivers each event as
                # soon as it is flushed; iteration buffers with read-ahead and
                # would stall real-time streaming.
                while True:
                    raw = proc.stdout.readline()
                    if not raw:
                        break
                    # Liveness: a line arrived, so the CLI isn't wedged — reset
                    # the inactivity watchdog.
                    inactivity.cancel()
                    inactivity = threading.Timer(
                        request.inactivity_timeout_seconds, _on_inactivity
                    )
                    inactivity.start()
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    events.append(event)
                    etype = event.get("type")
                    if etype == "result":
                        result_event = event
                    elif etype == "rate_limit_event":
                        last_rate_limit = event.get("rate_limit_info", {})
                    # Bridge onto the loop; fire-and-forget, the consumer
                    # throttles. Never let a callback error kill the read loop.
                    try:
                        asyncio.run_coroutine_threadsafe(_relay(on_event, event), loop)
                    except RuntimeError:
                        pass
                proc.wait()
            finally:
                watchdog.cancel()
                inactivity.cancel()
                if on_spawn is not None:
                    on_spawn(None)
            exit_code = proc.returncode
            stderr_f.seek(0)
            stderr_text = stderr_f.read().decode("utf-8", errors="replace")

        duration = monotonic() - started
        wedged = timed_out.is_set() or stalled.is_set()
        return self._finalize(
            request, events, result_event, last_rate_limit, duration,
            None if wedged else exit_code, stderr_text,
            timed_out=wedged,
        )

    def _finalize(
        self,
        request: ClaudeRunRequest,
        events: list[dict[str, Any]],
        result_event: dict[str, Any] | None,
        last_rate_limit: dict[str, Any] | None,
        duration: float,
        exit_code: int | None,
        stderr_text: str,
        *,
        timed_out: bool,
    ) -> ClaudeRunResult:
        resets_at = last_rate_limit.get("resetsAt") if last_rate_limit else None
        blocking_rl = last_rate_limit is not None and _rate_limit_blocking(last_rate_limit)

        if timed_out:
            return ClaudeRunResult(
                success=False,
                final_message=None,
                error=(
                    "rate limited — window exhausted"
                    if blocking_rl
                    else f"timeout after {request.timeout_seconds}s"
                ),
                duration_seconds=duration,
                exit_code=None,
                events=events,
                stderr=stderr_text,
                rate_limited=blocking_rl,
                rate_limit_resets_at=resets_at if blocking_rl else None,
            )

        # A completed run that nonetheless hit a blocking rate-limit event.
        if blocking_rl and (result_event is None or result_event.get("subtype") != "success"):
            logger.warning("claude.run.rate_limited resets_at={r}", r=resets_at)
            return ClaudeRunResult(
                success=False,
                final_message=None,
                error="rate limited — window exhausted",
                duration_seconds=duration,
                exit_code=exit_code,
                events=events,
                stderr=stderr_text,
                rate_limited=True,
                rate_limit_resets_at=resets_at,
            )

        if exit_code not in (0, None):
            return ClaudeRunResult(
                success=False,
                final_message=None,
                error=f"exit code {exit_code}",
                duration_seconds=duration,
                exit_code=exit_code,
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
            exit_code=exit_code,
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
            # Ignore any .mcp.json shipped inside the target repo (e.g. a
            # postgres MCP pointing at localhost:6432). Without this, Claude
            # Code auto-loads the cloned repo's MCP config and hangs on the
            # unreachable server during init. The agent uses its own
            # allowed-tools, not the repo's MCP servers.
            "--strict-mcp-config",
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
