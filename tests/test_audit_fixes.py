"""Unit tests for audit-fix helpers: branch-slug safety and JSON extraction."""

from __future__ import annotations

from ai_agent.ai.reviewer import _extract_last_json
from ai_agent.git_ops.worktree import make_branch_slug


def test_slug_basic_ascii() -> None:
    assert make_branch_slug(456, "Add metrics endpoint") == "T-456-add-metrics-endpoint"


def test_slug_cyrillic_title_does_not_collapse_to_task() -> None:
    # All-Cyrillic titles previously sanitised to the literal slug 'task',
    # colliding across distinct tasks. Now they get a stable hashed suffix.
    a = make_branch_slug(None, "проверка сетевых доступов")
    b = make_branch_slug(None, "другая кириллическая задача")
    assert a != b
    assert a.startswith("task-") and b.startswith("task-")
    # With a task id the id disambiguates and is preserved.
    assert make_branch_slug(664, "проверка").startswith("T-664-task-")


def test_slug_strips_invalid_refname_parts() -> None:
    # '..', trailing '.', and '.lock' are illegal in git refnames.
    assert ".." not in make_branch_slug(1, "a..b")
    assert not make_branch_slug(2, "weird.lock").endswith(".lock")
    assert not make_branch_slug(3, "...").endswith(".")


def test_extract_last_json_handles_braces_in_strings() -> None:
    obj = '{"verdict": "request_changes", "summary": "fix the } brace and {x}"}'
    got = _extract_last_json(f"Here is my verdict.\n{obj}")
    assert got == obj


def test_extract_last_json_picks_last_top_level_object() -> None:
    msg = '{"a": 1}\nsome prose\n{"verdict": "approve", "blocking_issues": [{"file": "x"}]}'
    got = _extract_last_json(msg)
    assert got is not None and '"verdict": "approve"' in got and got.endswith("}")


def test_extract_last_json_none_when_no_object() -> None:
    assert _extract_last_json("no json here") is None
