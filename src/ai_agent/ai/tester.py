"""Tester agent: a QA pass that runs AFTER the reviewer approves.

It writes focused tests that exercise the behaviour the diff changes, runs them
with pytest in the worktree, and returns a structured verdict. Unlike the
reviewer it CAN write (test files only) and run commands, but it must never
touch the implementation. A failing verdict bounces the diff back to the coder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import BaseModel, ValidationError

from ai_agent.ai.claude_runner import (
    ClaudeRunner,
    ClaudeRunRequest,
    EventCallback,
    SpawnCallback,
)
from ai_agent.ai.reviewer import _extract_last_json
from ai_agent.errors import AgentError

TESTER_SYSTEM_PROMPT = """\
You are a senior QA engineer. The diff below has already passed code review;
your job is to PROVE it works by writing and running tests.

The task brief below contains the FULL task (title + description / acceptance
criteria) — test the behaviour the task actually REQUIRES, not just whatever the
diff happens to do. If the diff does not implement what the brief asks, your
tests for the required behaviour should fail (verdict "fail").

Process:
- Write focused tests that exercise the behaviour this diff changes — happy path
  plus the meaningful edge cases. Follow the repo's existing test layout, naming,
  and fixtures (read neighbouring tests first).
- You MAY create/extend test files and run commands. You must NOT modify the
  implementation under test, or any non-test file, in any way. If the change
  looks buggy, do not fix it — fail the verdict and explain.
- Run your tests with `uv run --frozen pytest <your test files>`. Use
  `uv run --frozen ...` so the environment is never re-locked; do not run
  `uv lock`/`uv sync` or edit `uv.lock`.
- Keep your tests lint-clean. A targeted inline `# noqa: <CODE>` on one line is
  fine for a spurious rule (e.g. `SLF001` when mocking a private member, `S101`
  for asserts); never blanket-disable rules or edit ruff config.
- The target app may need services (DB/Redis/etc.) that are not available here.
  If you genuinely cannot run a test in isolation, prefer unit-level tests with
  mocks. If nothing can be run, set "ran": false and explain why.
- Commit the tests you add with a concise conventional-commit message.

Reply with EXACTLY one JSON object on the LAST line of your message, nothing
after it. No code fences around it. Shape (emit on a single line):

{
  "verdict": "pass" | "fail",
  "summary": "<one sentence>",
  "tests_added": ["<path>", "..."],
  "ran": true | false,
  "failures": [{"test": "<name or path>", "detail": "<what failed and why>"}]
}

- "pass": the tests you wrote and RAN pass. Also use "pass" with "ran": false if
  the change simply cannot be exercised in this environment (missing services,
  no way to import the module in isolation) — do NOT block the pipeline on
  infrastructure you cannot control; explain in the summary.
- "fail": ONLY when a test you actually RAN fails, proving the implementation is
  wrong. List each failing test under failures. Never fail a run just because you
  could not run the tests.
"""


Verdict = Literal["pass", "fail"]


class TestFailure(BaseModel):
    test: str
    detail: str


class TesterVerdict(BaseModel):
    verdict: Verdict
    summary: str
    tests_added: list[str] = []
    ran: bool = False
    failures: list[TestFailure] = []

    @property
    def is_passed(self) -> bool:
        return self.verdict == "pass"

    def feedback_for_claude(self) -> str:
        if self.verdict == "pass":
            return "QA tests passed."
        lines = [
            "A QA agent wrote tests for your change and they FAIL — your "
            "implementation is wrong. Fix the implementation so these tests pass.",
            "Do NOT modify, weaken, or delete the tests.",
            "",
            f"Summary: {self.summary}",
        ]
        if self.tests_added:
            lines.append(f"Tests added: {', '.join(self.tests_added)}")
        if self.failures:
            lines.append("")
            lines.append("Failing tests:")
            for i, f in enumerate(self.failures, 1):
                lines.append(f"{i}. {f.test} — {f.detail}")
        return "\n".join(lines)


class TesterAgent:
    ALLOWED_TOOLS: tuple[str, ...] = (
        "Read",
        "Glob",
        "Grep",
        "Edit",
        "Write",
        "MultiEdit",
        "Bash(uv:*)",
        "Bash(pytest:*)",
        "Bash(python:*)",
        "Bash(python3:*)",
        "Bash(ls:*)",
        "Bash(cat:*)",
        "Bash(find:*)",
        "Bash(grep:*)",
        "Bash(git add:*)",
        "Bash(git commit:*)",
    )
    DENIED_TOOLS: tuple[str, ...] = (
        "Bash(git push:*)",
        "Bash(rm -rf:*)",
        "Bash(sudo:*)",
    )

    def __init__(self, runner: ClaudeRunner, model: str | None = None) -> None:
        self._runner = runner
        self._model = model

    async def test(
        self,
        worktree_path: Path,
        diff_text: str,
        task_brief: str,
        timeout_seconds: int = 1200,
        on_event: EventCallback | None = None,
        on_spawn: SpawnCallback | None = None,
    ) -> TesterVerdict:
        prompt = self._build_prompt(task_brief, diff_text)
        result = await self._runner.run(
            ClaudeRunRequest(
                cwd=worktree_path,
                prompt=prompt,
                system_prompt=TESTER_SYSTEM_PROMPT,
                model=self._model,
                allowed_tools=self.ALLOWED_TOOLS,
                disallowed_tools=self.DENIED_TOOLS,
                permission_mode="acceptEdits",
                timeout_seconds=timeout_seconds,
            ),
            on_event=on_event,
            on_spawn=on_spawn,
        )
        if not result.success or not result.final_message:
            raise AgentError(
                "tester run failed",
                details={"error": result.error, "stderr_tail": result.stderr[-500:]},
            )
        return self._parse_verdict(result.final_message)

    @staticmethod
    def _build_prompt(task_brief: str, diff_text: str) -> str:
        return (
            "## Task brief\n"
            f"{task_brief.strip()}\n\n"
            "## Diff under test (already code-reviewed)\n"
            "```diff\n"
            f"{diff_text.strip()}\n"
            "```\n\n"
            "Write and run tests per your system prompt. Output the JSON verdict "
            "as the LAST line."
        )

    @staticmethod
    def _parse_verdict(message: str) -> TesterVerdict:
        json_str = _extract_last_json(message)
        if json_str is None:
            logger.warning("tester.no_json msg_tail={t}", t=message[-300:])
            raise AgentError(
                "tester did not return JSON",
                details={"final_message_tail": message[-500:]},
            )
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise AgentError(
                "tester JSON parse failed",
                details={"error": str(e), "raw": json_str[:500]},
            ) from e
        try:
            return TesterVerdict(**data)
        except ValidationError as e:
            raise AgentError(
                "tester JSON did not match schema",
                details={"error": str(e), "raw": json_str[:500]},
            ) from e
