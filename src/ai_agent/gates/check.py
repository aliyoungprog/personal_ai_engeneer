"""Quality gates: run lint / typecheck / tests in a worktree.

Returns a structured GateSuite with pass/fail per gate + truncated output.
The output tails are designed to be fed back to Claude as iteration feedback
if any gate fails.

Two scoping rules keep the gates fair on real (often legacy) target repos:
  A. If a tool isn't available (`uv run --frozen <tool>` cannot spawn it), the
     gate is SKIPPED, not failed — the coder cannot fix a missing tool.
  B. Lint/typecheck only BLOCK on diagnostics on the lines this change added or
     modified; a file's pre-existing errors never fail the run.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from time import monotonic
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict

# uv prints this to stderr when the requested tool isn't installed in the env.
_SPAWN_FAILURE_MARKER = "Failed to spawn"
# A mypy diagnostic line: "path/to/file.py:123: error: message"
_MYPY_LINE_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?:\d+:)?\s*error:\s*(?P<msg>.*)$")


class GateResult(BaseModel):
    name: str
    passed: bool
    duration_seconds: float
    exit_code: int | None
    stdout_tail: str
    stderr_tail: str
    timed_out: bool = False
    skipped: bool = False
    skip_reason: str | None = None


class GateSuite(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    cwd: Path
    results: list[GateResult]

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[GateResult]:
        return [r for r in self.results if not r.passed and not r.skipped]

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
        lines.append(
            "These are errors on the lines your change touched. Fix them in your "
            "changed source — do NOT add dependencies, tooling, or config to make "
            "a check pass, and do not touch unrelated code."
        )
        return "\n".join(lines)


def _is_test_file(p: Path) -> bool:
    return p.name.startswith("test_") or p.name.endswith("_test.py") or "tests" in p.parts


def _python_targets(cwd: Path, changed_files: list[str]) -> list[str]:
    """Changed .py files that still exist in the worktree (skip deletions)."""

    return [f for f in changed_files if f.endswith(".py") and (cwd / f).is_file()]


def _select_test_targets(cwd: Path, py_files: list[str]) -> list[str]:
    """Map changed .py files to the tests worth running.

    A changed test file runs itself; a changed source file pulls in tests named
    `test_<stem>.py` / `<stem>_test.py` anywhere in the tree. Best-effort: if a
    source change has no discoverable test, it simply contributes none.
    """

    targets: set[str] = set()
    for f in py_files:
        p = Path(f)
        if _is_test_file(p):
            targets.add(f)
            continue
        for m in [*cwd.glob(f"**/test_{p.stem}.py"), *cwd.glob(f"**/{p.stem}_test.py")]:
            if ".git" not in m.parts:
                targets.add(str(m.relative_to(cwd)))
    return sorted(targets)


def _norm_path(filename: str, cwd: Path) -> str:
    """Normalise a tool-reported path to a repo-relative POSIX string."""

    p = Path(filename)
    if p.is_absolute():
        try:
            return os.path.relpath(p, cwd).replace(os.sep, "/")
        except ValueError:
            return p.as_posix()
    return p.as_posix().removeprefix("./")


def _in_scope(rel_path: str, row: int, changed_lines: dict[str, set[int]]) -> bool:
    """True if a diagnostic at rel_path:row is on a line this change touched.

    With no change-line info at all we cannot scope, so nothing is filtered
    (fail-safe to whole-file behaviour). With info present, a file absent from
    the map had only deletions → none of its diagnostics are in scope.
    """

    if not changed_lines:
        return True
    return row in changed_lines.get(rel_path, set())


def _filter_ruff(
    stdout: str, changed_lines: dict[str, set[int]], cwd: Path
) -> list[dict[str, Any]]:
    """Parse ruff --output-format=json and keep only in-scope diagnostics."""

    data = json.loads(stdout) if stdout.strip() else []
    kept: list[dict[str, Any]] = []
    for d in data:
        loc = d.get("location") or {}
        row = loc.get("row")
        rel = _norm_path(str(d.get("filename", "")), cwd)
        if isinstance(row, int) and _in_scope(rel, row, changed_lines):
            kept.append(d)
    return kept


def _format_ruff(diags: list[dict[str, Any]]) -> str:
    out = []
    for d in diags:
        loc = d.get("location") or {}
        where = f"{d.get('filename')}:{loc.get('row')}:{loc.get('column')}"
        out.append(f"{where} {d.get('code') or '?'} {d.get('message')}")
    return "\n".join(out)


def _filter_mypy(stdout: str, changed_lines: dict[str, set[int]], cwd: Path) -> list[str]:
    """Keep only mypy error lines whose location is on a changed line."""

    kept: list[str] = []
    for line in stdout.splitlines():
        m = _MYPY_LINE_RE.match(line)
        if not m:
            continue
        rel = _norm_path(m.group("file"), cwd)
        if _in_scope(rel, int(m.group("line")), changed_lines):
            kept.append(line)
    return kept


class QualityGates:
    """Runs lint/typecheck/test scoped to the lines a run actually changed."""

    def __init__(self, test_timeout_seconds: int = 600) -> None:
        self._test_timeout = test_timeout_seconds

    async def run(
        self,
        cwd: Path,
        changed_files: list[str],
        changed_lines: dict[str, set[int]] | None = None,
    ) -> GateSuite:
        py_files = _python_targets(cwd, changed_files)
        if not py_files:
            logger.info("gates.skip reason=no_python_changes changed={n}", n=len(changed_files))
            return GateSuite(cwd=cwd, results=[])
        scope = changed_lines or {}
        logger.info("gates.scope py_files={n}", n=len(py_files))
        results = [
            await self._gate_ruff(cwd, py_files, scope),
            await self._gate_mypy(cwd, py_files, scope),
        ]
        test_targets = _select_test_targets(cwd, py_files)
        if test_targets:
            results.append(await self._gate_pytest(cwd, test_targets))
        else:
            logger.info("gates.test_skip reason=no_matching_tests files={n}", n=len(py_files))
        return GateSuite(cwd=cwd, results=results)

    async def _exec(
        self, command: tuple[str, ...], cwd: Path, timeout_s: int
    ) -> tuple[int | None, str, str, float, bool]:
        logger.info("gates.run cmd={c}", c=" ".join(command))
        started = monotonic()
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ},
        )
        timed_out = False
        out_b = err_b = b""
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            timed_out = True
            proc.kill()
            await proc.wait()
        duration = monotonic() - started
        exit_code = proc.returncode if not timed_out else None
        return (
            exit_code,
            out_b.decode("utf-8", errors="replace"),
            err_b.decode("utf-8", errors="replace"),
            duration,
            timed_out,
        )

    @staticmethod
    def _skipped(name: str, reason: str, duration: float, stderr: str) -> GateResult:
        logger.info("gates.skip_tool name={n} reason={r}", n=name, r=reason)
        return GateResult(
            name=name,
            passed=True,
            skipped=True,
            skip_reason=reason,
            duration_seconds=duration,
            exit_code=None,
            stdout_tail="",
            stderr_tail=_tail(stderr, 20),
        )

    async def _gate_ruff(
        self, cwd: Path, py_files: list[str], scope: dict[str, set[int]]
    ) -> GateResult:
        cmd = ("uv", "run", "--frozen", "ruff", "check", "--output-format=json", *py_files)
        exit_code, stdout, stderr, duration, timed_out = await self._exec(cmd, cwd, 300)
        if _SPAWN_FAILURE_MARKER in stderr:
            return self._skipped("lint", "ruff not available", duration, stderr)
        try:
            kept = _filter_ruff(stdout, scope, cwd)
        except json.JSONDecodeError:
            # ruff couldn't emit JSON (usage/config error) — surface, don't hide.
            logger.warning("gates.ruff_bad_output exit={e}", e=exit_code)
            return GateResult(
                name="lint",
                passed=False,
                duration_seconds=duration,
                exit_code=exit_code,
                stdout_tail=_tail(stdout, 40),
                stderr_tail=_tail(stderr, 40),
                timed_out=timed_out,
            )
        passed = not kept and not timed_out
        logger.info("gates.done name=lint passed={p} in_scope={k}", p=passed, k=len(kept))
        return GateResult(
            name="lint",
            passed=passed,
            duration_seconds=duration,
            exit_code=exit_code,
            stdout_tail=_tail(_format_ruff(kept), 80),
            stderr_tail="",
            timed_out=timed_out,
        )

    async def _gate_mypy(
        self, cwd: Path, py_files: list[str], scope: dict[str, set[int]]
    ) -> GateResult:
        cmd = ("uv", "run", "--frozen", "mypy", *py_files)
        exit_code, stdout, stderr, duration, timed_out = await self._exec(cmd, cwd, 300)
        if _SPAWN_FAILURE_MARKER in stderr or _SPAWN_FAILURE_MARKER in stdout:
            return self._skipped("typecheck", "mypy not available", duration, stderr)
        kept = _filter_mypy(stdout, scope, cwd)
        passed = not kept and not timed_out
        logger.info("gates.done name=typecheck passed={p} in_scope={k}", p=passed, k=len(kept))
        return GateResult(
            name="typecheck",
            passed=passed,
            duration_seconds=duration,
            exit_code=exit_code,
            stdout_tail=_tail("\n".join(kept), 80),
            stderr_tail="" if passed else _tail(stderr, 20),
            timed_out=timed_out,
        )

    async def _gate_pytest(self, cwd: Path, test_targets: list[str]) -> GateResult:
        cmd = ("uv", "run", "--frozen", "pytest", "-q", *test_targets)
        exit_code, stdout, stderr, duration, timed_out = await self._exec(
            cmd, cwd, self._test_timeout
        )
        if _SPAWN_FAILURE_MARKER in stderr:
            return self._skipped("test", "pytest not available", duration, stderr)
        passed = not timed_out and exit_code == 0
        logger.info("gates.done name=test passed={p} duration={d}s", p=passed, d=round(duration, 2))
        return GateResult(
            name="test",
            passed=passed,
            duration_seconds=duration,
            exit_code=exit_code,
            stdout_tail=_tail(stdout, 80),
            stderr_tail=_tail(stderr, 80),
            timed_out=timed_out,
        )


def _tail(text: str, n: int) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= n:
        return text
    return "\n".join(lines[-n:])
