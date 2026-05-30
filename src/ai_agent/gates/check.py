"""Quality gates: run lint / typecheck / tests in a worktree.

Returns a structured GateSuite with pass/fail per gate + truncated output.
The output tails are designed to be fed back to Claude as iteration feedback
if any gate fails.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from time import monotonic

from loguru import logger
from pydantic import BaseModel, ConfigDict


class GateSpec(BaseModel):
    """How to run one quality gate."""

    name: str
    command: tuple[str, ...]
    timeout_seconds: int = 300
    tail_lines: int = 80


class GateResult(BaseModel):
    name: str
    passed: bool
    duration_seconds: float
    exit_code: int | None
    stdout_tail: str
    stderr_tail: str
    timed_out: bool = False


class GateSuite(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    cwd: Path
    results: list[GateResult]

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[GateResult]:
        return [r for r in self.results if not r.passed]

    def feedback_for_claude(self) -> str:
        """Format failed gates as a prompt fragment to send back to Claude."""

        if self.all_passed:
            return "All quality gates passed."
        lines = ["The following quality gates failed:\n"]
        for r in self.failures:
            lines.append(f"## {r.name} (exit_code={r.exit_code})")
            if r.timed_out:
                lines.append(f"Timed out after {r.duration_seconds:.0f}s.")
            if r.stdout_tail.strip():
                lines.append("stdout (last lines):")
                lines.append("```")
                lines.append(r.stdout_tail.strip())
                lines.append("```")
            if r.stderr_tail.strip():
                lines.append("stderr (last lines):")
                lines.append("```")
                lines.append(r.stderr_tail.strip())
                lines.append("```")
            lines.append("")
        lines.append("Please fix these issues. Do not change unrelated code.")
        return "\n".join(lines)


DEFAULT_PYTHON_GATES: tuple[GateSpec, ...] = (
    GateSpec(name="lint", command=("uv", "run", "ruff", "check", ".")),
    GateSpec(name="typecheck", command=("uv", "run", "mypy", ".")),
    GateSpec(name="test", command=("uv", "run", "pytest", "-q"), timeout_seconds=600),
)


class QualityGates:
    """Runs a fixed sequence of GateSpec commands against a worktree."""

    def __init__(self, gates: tuple[GateSpec, ...] = DEFAULT_PYTHON_GATES) -> None:
        self._gates = gates

    async def run(self, cwd: Path) -> GateSuite:
        results: list[GateResult] = []
        for spec in self._gates:
            result = await self._run_one(spec, cwd)
            results.append(result)
        return GateSuite(cwd=cwd, results=results)

    async def _run_one(self, spec: GateSpec, cwd: Path) -> GateResult:
        logger.info("gates.run name={n} cmd={c}", n=spec.name, c=" ".join(spec.command))
        started = monotonic()
        proc = await asyncio.create_subprocess_exec(
            *spec.command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ},
        )
        timed_out = False
        stdout_bytes = b""
        stderr_bytes = b""
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=spec.timeout_seconds,
            )
        except TimeoutError:
            timed_out = True
            proc.kill()
            await proc.wait()
        duration = monotonic() - started

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = proc.returncode if not timed_out else None
        passed = not timed_out and exit_code == 0

        logger.info(
            "gates.done name={n} passed={p} duration={d}s",
            n=spec.name,
            p=passed,
            d=round(duration, 2),
        )
        return GateResult(
            name=spec.name,
            passed=passed,
            duration_seconds=duration,
            exit_code=exit_code,
            stdout_tail=_tail(stdout, spec.tail_lines),
            stderr_tail=_tail(stderr, spec.tail_lines),
            timed_out=timed_out,
        )


def _tail(text: str, n: int) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= n:
        return text
    return "\n".join(lines[-n:])
