"""Build the initial coder prompt and the MR description from a Task."""

from __future__ import annotations

from ai_agent.ai import ReviewVerdict
from ai_agent.database.models import Task
from ai_agent.gates import GateSuite


def build_coder_prompt(task: Task, body: str = "") -> str:
    tid = f"T-{task.notion_task_id}" if task.notion_task_id else task.notion_page_id[:8]
    parts = [
        f"You are implementing task **{tid}**.",
        "",
        f"## Title\n{task.title}",
    ]
    if body.strip():
        parts += ["", "## Description", body.strip()]
    parts += [
        "",
        "## Working agreement",
        "- Read `CLAUDE.md` and `AGENTS.md` in this repo first if present.",
        "- Stay strictly within the scope of this task; do not refactor unrelated code.",
        "- Add or update tests for any new logic.",
        "- Run `uv run ruff check .` and `uv run pytest -q` before declaring success.",
        "- Commit your changes with a concise conventional-commit message.",
        "- When done, reply with a one-line summary of what you changed.",
    ]
    return "\n".join(parts)


def build_mr_description(
    task: Task,
    verdict: ReviewVerdict,
    gates: GateSuite,
    notion_url: str,
) -> str:
    tid = f"T-{task.notion_task_id}" if task.notion_task_id else task.notion_page_id[:8]
    lines = [
        f"### Task: {tid} — {task.title}",
        f"Notion: {notion_url}",
        "",
        "### Reviewer verdict",
        f"- **{verdict.verdict}** — {verdict.summary}",
    ]
    if verdict.non_blocking:
        lines.append("- Non-blocking suggestions deferred:")
        for f in verdict.non_blocking:
            lines.append(f"  - {f.file}: {f.issue}")
    lines += [
        "",
        "### Quality gates",
    ]
    for r in gates.results:
        mark = "✅" if r.passed else "❌"
        lines.append(f"- {mark} {r.name} ({r.duration_seconds:.0f}s)")
    lines += [
        "",
        "---",
        "🤖 Created by [personal_ai_engeneer](https://github.com/aliyoungprog/personal_ai_engeneer)",
    ]
    return "\n".join(lines)
