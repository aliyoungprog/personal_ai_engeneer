from datetime import datetime
from enum import StrEnum
from typing import Any

from tortoise import fields

from ai_agent.database.models._base import BaseAbstractModel
from ai_agent.database.models.tasks import Task


class TaskRunStatus(StrEnum):
    PENDING = "pending"
    PREPARING = "preparing"          # cloning / worktree setup
    CODING = "coding"                # claude generating code
    TESTING = "testing"              # ruff / mypy / pytest in worktree
    REVIEWING = "reviewing"          # reviewer agent
    PUSHING = "pushing"              # push branch + create MR
    AWAITING_CI = "awaiting_ci"      # GitLab pipeline pending
    AWAITING_APPROVAL = "awaiting_approval"  # human in Telegram
    MERGING = "merging"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskRun(BaseAbstractModel):
    """Single execution attempt of a Task by the coding agent.

    Holds FSM state, branch / MR identifiers, iteration counters, and a
    JSON audit log of significant stage transitions.
    """

    task: fields.ForeignKeyRelation[Task] = fields.ForeignKeyField(
        "models.Task",
        related_name="runs",
        on_delete=fields.CASCADE,
    )
    status: str = fields.CharField(
        max_length=32,
        default=TaskRunStatus.PENDING.value,
        index=True,
    )
    branch_name: str | None = fields.CharField(max_length=255, null=True)
    worktree_path: str | None = fields.TextField(null=True)
    mr_url: str | None = fields.TextField(null=True)
    mr_iid: int | None = fields.IntField(null=True)
    iterations_code: int = fields.IntField(default=0)
    iterations_review: int = fields.IntField(default=0)
    error_message: str | None = fields.TextField(null=True)
    started_at: datetime = fields.DatetimeField(auto_now_add=True)
    finished_at: datetime | None = fields.DatetimeField(null=True)
    events: list[dict[str, Any]] = fields.JSONField(default=list)

    class Meta:
        table = "task_runs"
