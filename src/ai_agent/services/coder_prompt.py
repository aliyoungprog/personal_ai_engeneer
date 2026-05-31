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
        "- Verify ONLY what you changed — do NOT lint or test the whole project.",
        "  Lint just the files you edited (`uv run --frozen ruff check <files>`), and run",
        "  only the tests that cover your change (`uv run --frozen pytest <test files>`).",
        "- Use `uv run --frozen ...` so the environment is not re-locked. Do NOT run",
        "  `uv lock`/`uv sync` or otherwise modify `uv.lock` unless you intentionally",
        "  changed dependencies in `pyproject.toml`.",
        "- Commit your changes with a concise conventional-commit message.",
        "- When done, reply with a one-line summary of what you changed.",
    ]
    return "\n".join(parts)


def build_fix_prompt(task: Task, feedback: str, body: str = "") -> str:
    """Prompt for a follow-up coder pass that addresses gate/reviewer feedback.

    The previous pass's changes are already committed in the worktree, so the
    coder builds on them rather than starting over.
    """

    tid = f"T-{task.notion_task_id}" if task.notion_task_id else task.notion_page_id[:8]
    parts = [
        f"You are continuing task **{tid}** — your previous attempt needs fixes.",
        "",
        f"## Title\n{task.title}",
    ]
    if body.strip():
        parts += ["", "## Description", body.strip()]
    parts += [
        "",
        "## Feedback to address",
        feedback.strip(),
        "",
        "## Working agreement",
        "- Your previous changes are already committed in this worktree. Build on them;",
        "  do NOT revert or rewrite unrelated parts of your own diff.",
        "- Address EVERY item in the feedback above, precisely and minimally — make",
        "  exactly the change asked for, nothing more. Do not expand scope or refactor",
        "  other code.",
        "- A failing quality gate must be fixed IN THE SOURCE you changed. Do NOT add or",
        "  upgrade dependencies, add tool config (e.g. `[tool.mypy]`), or touch `uv.lock`",
        "  to make a check pass — that is scope creep and will be rejected. Only modify",
        "  `pyproject.toml`/`uv.lock` if the task itself is about dependencies.",
        "- Add or update tests for any new logic.",
        "- Verify ONLY what you changed: lint the edited files (`uv run --frozen ruff check",
        "  <files>`) and run only the matching tests (`uv run --frozen pytest <test files>`).",
        "- Use `uv run --frozen ...`; do NOT run `uv lock`/`uv sync`.",
        "- Commit your fixes with a concise conventional-commit message.",
        "- When done, reply with a one-line summary of what you fixed.",
    ]
    return "\n".join(parts)


def build_mr_description(
    task: Task,
    verdict: ReviewVerdict,
    gates: GateSuite,
    notion_url: str,
    changed_files: list[str] | None = None,
) -> str:
    tid = f"T-{task.notion_task_id}" if task.notion_task_id else task.notion_page_id[:8]
    changed_files = changed_files or []
    py_files = [f for f in changed_files if f.endswith(".py")]

    lines = [
        f"### Task: {tid} — {task.title}",
        f"Notion: {notion_url}",
        "",
        f"### Changed files ({len(changed_files)})",
    ]
    lines += [f"- `{f}`" for f in changed_files] or ["- (none reported)"]

    lines += ["", "### How this was verified"]
    if gates.results:
        passed = sum(r.passed for r in gates.results)
        lines.append(
            f"Ran {len(gates.results)} quality gate(s) on {len(py_files)} changed "
            f"Python file(s) — {passed}/{len(gates.results)} passed. "
            "No app run / integration test was performed."
        )
    else:
        lines.append(
            f"No Python files changed ({len(changed_files)} file(s) touched), so "
            "lint/typecheck/tests were skipped. Change verified by reviewer only — "
            "no automated test covers it."
        )

    lines += ["", "### Quality gates"]
    if gates.results:
        for r in gates.results:
            mark = "✅" if r.passed else "❌"
            status = "timed out" if r.timed_out else f"exit {r.exit_code}"
            lines.append(f"- {mark} `{r.name}` — {status} ({r.duration_seconds:.0f}s)")
    else:
        lines.append("- ⏭ skipped — no Python changes to lint or test")

    lines += ["", "### Reviewer verdict", f"- **{verdict.verdict}** — {verdict.summary}"]
    if verdict.non_blocking:
        lines.append("- Non-blocking suggestions deferred:")
        for f in verdict.non_blocking:
            lines.append(f"  - `{f.file}`: {f.issue}")

    return "\n".join(lines)
