"""Reviewer agent: second Claude pass to adversarially check the coder's diff.

Read-only — no Edit/Write/Bash allowed. Returns a structured verdict.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import BaseModel, ValidationError

from ai_agent.ai.claude_runner import ClaudeRunner, ClaudeRunRequest
from ai_agent.errors import AgentError

REVIEWER_SYSTEM_PROMPT = """\
You are a strict senior software engineer doing a code review.

Rules:
- You have READ-ONLY access. You may read files with Read/Glob/Grep but
  must NOT propose fixes or modify anything.
- Judge the diff against these criteria:
  1. Correctness — bugs, off-by-one, race conditions, wrong logic.
  2. Scope — does the change touch only what the task asks? Flag unrelated edits.
  3. Tests — are new tests present where they should be?
  4. Project conventions — does the diff follow the repo's CLAUDE.md / AGENTS.md
     and existing code style?
  5. Safety — secrets/PII leaks, destructive operations, blocking calls in async.

Reply with EXACTLY one JSON object on the LAST line of your message, nothing
after it. No code fences around it. Shape (formatted here for readability,
emit it on a single line):

{
  "verdict": "approve" | "request_changes" | "comment",
  "summary": "<one sentence>",
  "blocking_issues": [{"file": "<path>", "lines": "<range or ?>", "issue": "<concise>"}],
  "non_blocking":    [{"file": "<path>", "issue": "<concise>"}]
}

- "approve": change is correct, scoped, complete.
- "request_changes": real bugs OR scope creep OR missing tests for new logic.
- "comment": acceptable but you have non-blocking suggestions.
"""


Verdict = Literal["approve", "request_changes", "comment"]


class ReviewFinding(BaseModel):
    file: str
    lines: str | None = None
    issue: str


class ReviewVerdict(BaseModel):
    verdict: Verdict
    summary: str
    blocking_issues: list[ReviewFinding] = []
    non_blocking: list[ReviewFinding] = []

    @property
    def is_approved(self) -> bool:
        return self.verdict == "approve"

    def feedback_for_claude(self) -> str:
        if self.verdict == "approve":
            return "Reviewer approved the change."
        lines = [f"Reviewer verdict: {self.verdict}", f"Summary: {self.summary}", ""]
        if self.blocking_issues:
            lines.append("Blocking issues to fix:")
            for i, f in enumerate(self.blocking_issues, 1):
                where = f"{f.file}:{f.lines}" if f.lines else f.file
                lines.append(f"{i}. {where} — {f.issue}")
        if self.non_blocking:
            lines.append("")
            lines.append("Non-blocking suggestions:")
            for f in self.non_blocking:
                lines.append(f"- {f.file}: {f.issue}")
        lines.append("")
        lines.append("Fix the blocking issues. Do not touch unrelated code.")
        return "\n".join(lines)


class ReviewerAgent:
    READ_ONLY_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep")
    DENIED_TOOLS: tuple[str, ...] = (
        "Edit",
        "Write",
        "MultiEdit",
        "Bash(git push:*)",
        "Bash(rm -rf:*)",
        "Bash(sudo:*)",
    )

    def __init__(self, runner: ClaudeRunner, model: str | None = None) -> None:
        self._runner = runner
        self._model = model

    async def review(
        self,
        worktree_path: Path,
        diff_text: str,
        task_brief: str,
        timeout_seconds: int = 600,
    ) -> ReviewVerdict:
        prompt = self._build_prompt(task_brief, diff_text)
        result = await self._runner.run(
            ClaudeRunRequest(
                cwd=worktree_path,
                prompt=prompt,
                system_prompt=REVIEWER_SYSTEM_PROMPT,
                model=self._model,
                allowed_tools=self.READ_ONLY_TOOLS,
                disallowed_tools=self.DENIED_TOOLS,
                permission_mode="default",
                timeout_seconds=timeout_seconds,
            )
        )
        if not result.success or not result.final_message:
            raise AgentError(
                "reviewer run failed",
                details={
                    "error": result.error,
                    "stderr_tail": result.stderr[-500:],
                },
            )
        return self._parse_verdict(result.final_message)

    @staticmethod
    def _build_prompt(task_brief: str, diff_text: str) -> str:
        return (
            "## Task brief\n"
            f"{task_brief.strip()}\n\n"
            "## Diff against base branch\n"
            "```diff\n"
            f"{diff_text.strip()}\n"
            "```\n\n"
            "Review the change against the criteria in your system prompt. "
            "Output the JSON verdict as the LAST line."
        )

    @staticmethod
    def _parse_verdict(message: str) -> ReviewVerdict:
        json_str = _extract_last_json(message)
        if json_str is None:
            logger.warning("reviewer.no_json msg_tail={t}", t=message[-300:])
            raise AgentError(
                "reviewer did not return JSON",
                details={"final_message_tail": message[-500:]},
            )
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise AgentError(
                "reviewer JSON parse failed",
                details={"error": str(e), "raw": json_str[:500]},
            ) from e
        try:
            return ReviewVerdict(**data)
        except ValidationError as e:
            raise AgentError(
                "reviewer JSON did not match schema",
                details={"error": str(e), "raw": json_str[:500]},
            ) from e


_JSON_OBJECT_RE = re.compile(r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}", re.DOTALL)


def _extract_last_json(text: str) -> str | None:
    """Find the last JSON object substring in text. Tolerates extra prose."""

    matches = _JSON_OBJECT_RE.findall(text)
    if not matches:
        return None
    return matches[-1]
