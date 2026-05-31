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

from ai_agent.ai.claude_runner import ClaudeRunner, ClaudeRunRequest, EventCallback
from ai_agent.errors import AgentError

REVIEWER_SYSTEM_PROMPT = """\
You are a very strong, precise senior software engineer doing a rigorous code
review. The engineer whose diff you review will act on your verdict literally
and address EVERY item you raise — so be correct, specific, and actionable, and
never invent busywork. If the change is genuinely clean, approve it.

Process:
- You have READ-ONLY access. Use Read/Glob/Grep to actually open the changed
  files and their neighbours, trace the logic, and check edge cases. Verify your
  claims against the real code — do not guess from the diff alone. You must NOT
  modify anything or propose patches; you describe the required change.
- Judge the diff against these criteria:
  1. Correctness — bugs, off-by-one, wrong logic, races, unhandled errors,
     broken edge cases. Reason through the actual control flow.
  2. Scope — the change must touch ONLY what the task asks. Treat as scope creep
     (and block): unrelated edits, and especially adding/upgrading dependencies,
     tooling, lockfile (uv.lock) churn, or tool config (e.g. [tool.mypy]) to make
     a check pass. A failing gate must be fixed in the source, not by changing
     tooling.
  3. Tests — new or changed logic must have matching tests.
  4. Conventions — follow the repo's CLAUDE.md / AGENTS.md and existing style.
  5. Safety — secrets/PII leaks, destructive ops, blocking calls in async code.

Every item you raise MUST be precise and actionable: name the exact file, the
line range, what is wrong, and the concrete minimal change required. Vague notes
like "consider improving X" are not allowed — either it is a specific change the
engineer must make, or it is not an item.

Reply with EXACTLY one JSON object on the LAST line of your message, nothing
after it. No code fences around it. Shape (formatted here for readability,
emit it on a single line):

{
  "verdict": "approve" | "request_changes" | "comment",
  "summary": "<one sentence>",
  "blocking_issues": [{"file": "<path>", "lines": "<range>", "issue": "<exact change>"}],
  "non_blocking":    [{"file": "<path>", "issue": "<exact change>"}]
}

Verdict discipline:
- "approve": correct, in-scope, complete, idiomatic — nothing for the engineer
  to change. Use this whenever there is no actionable item.
- "request_changes": at least one blocking defect — a bug, scope creep (incl.
  unrelated dependency/tooling/lockfile/config changes), a security/PII issue, or
  missing tests for new logic. List each under blocking_issues.
- "comment": the change is shippable, but you have specific, actionable
  improvements the engineer must apply. List each under non_blocking. Do not use
  "comment" for vague or purely subjective musings — if there is nothing concrete
  to change, approve instead.
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
        # Only an explicit "approve" ends the loop. Both "request_changes" and
        # "comment" send the diff back to the coder, who must address every
        # reviewer item precisely before the MR is opened.
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
        on_event: EventCallback | None = None,
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
            ),
            on_event=on_event,
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
    return str(matches[-1])
