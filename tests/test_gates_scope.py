"""Unit tests for the gate scoping rules (A: skip missing tool, B: diff-scoped).

These cover the pure parsing/filtering helpers — no subprocess or git needed.
The behaviour they encode is what kept task 521 from ever passing: ruff lint of
a 2100-line legacy file reported 252 pre-existing errors, and mypy wasn't even
installed in the target repo's env.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_agent.gates.check import (
    GateResult,
    GateSuite,
    _filter_mypy,
    _filter_ruff,
    _in_scope,
    _norm_path,
)
from ai_agent.git_ops.worktree import parse_changed_lines

CWD = Path("/app/wt")


def test_parse_changed_lines_records_only_new_side_additions() -> None:
    diff = (
        "diff --git a/app/foo.py b/app/foo.py\n"
        "--- a/app/foo.py\n"
        "+++ b/app/foo.py\n"
        "@@ -10,2 +10,3 @@\n"
        "@@ -40 +41,0 @@\n"  # pure deletion on new side (count 0) — ignored
        "diff --git a/app/bar.py b/app/bar.py\n"
        "--- a/app/bar.py\n"
        "+++ b/app/bar.py\n"
        "@@ -1 +1 @@\n"
    )
    result = parse_changed_lines(diff)
    assert result["app/foo.py"] == {10, 11, 12}
    assert result["app/bar.py"] == {1}


def test_parse_changed_lines_skips_deleted_files() -> None:
    diff = "--- a/gone.py\n+++ /dev/null\n@@ -1,5 +0,0 @@\n"
    assert parse_changed_lines(diff) == {}


def test_norm_path_makes_absolute_repo_relative() -> None:
    assert _norm_path("/app/wt/app/foo.py", CWD) == "app/foo.py"
    assert _norm_path("./app/foo.py", CWD) == "app/foo.py"
    assert _norm_path("app/foo.py", CWD) == "app/foo.py"


def test_in_scope_filters_by_changed_line() -> None:
    scope = {"app/foo.py": {10, 11}}
    assert _in_scope("app/foo.py", 10, scope) is True
    assert _in_scope("app/foo.py", 99, scope) is False
    # File absent from the map had only deletions → nothing in scope.
    assert _in_scope("app/other.py", 1, scope) is False
    # No scope info at all → do not filter (fail-safe to whole-file).
    assert _in_scope("app/foo.py", 99, {}) is True


def test_filter_ruff_keeps_only_diagnostics_on_changed_lines() -> None:
    stdout = json.dumps(
        [
            {"filename": "/app/wt/app/foo.py", "code": "E501", "message": "long",
             "location": {"row": 11, "column": 5}},
            {"filename": "/app/wt/app/foo.py", "code": "COM812", "message": "comma",
             "location": {"row": 2000, "column": 1}},  # pre-existing, untouched line
        ]
    )
    scope = {"app/foo.py": {10, 11}}
    kept = _filter_ruff(stdout, scope, CWD)
    assert len(kept) == 1
    assert kept[0]["code"] == "E501"


def test_filter_mypy_keeps_only_errors_on_changed_lines() -> None:
    stdout = (
        "app/foo.py:11: error: Incompatible return value type\n"
        "app/foo.py:2000: error: Name 'x' is not defined\n"  # untouched legacy line
        "app/foo.py:11: note: see docs\n"  # notes are not errors → ignored
        "Found 2 errors in 1 file\n"
    )
    scope = {"app/foo.py": {11}}
    kept = _filter_mypy(stdout, scope, CWD)
    assert kept == ["app/foo.py:11: error: Incompatible return value type"]


def test_skipped_gate_does_not_count_as_failure() -> None:
    suite = GateSuite(
        cwd=CWD,
        results=[
            GateResult(
                name="typecheck", passed=True, skipped=True, skip_reason="mypy not available",
                duration_seconds=0.0, exit_code=None, stdout_tail="", stderr_tail="",
            ),
            GateResult(
                name="lint", passed=True, duration_seconds=0.1, exit_code=0,
                stdout_tail="", stderr_tail="",
            ),
        ],
    )
    assert suite.all_passed is True
    assert suite.failures == []
