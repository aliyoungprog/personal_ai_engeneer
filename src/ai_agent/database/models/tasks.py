from datetime import datetime
from enum import StrEnum

from tortoise import fields

from ai_agent.database.models._base import BaseAbstractModel


class TaskDecision(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    SKIPPED = "skipped"
    DEFERRED = "deferred"
    REMOVED = "removed"


class Task(BaseAbstractModel):
    """Task discovered in Notion that may become a coding job."""

    notion_page_id: str = fields.CharField(max_length=64, unique=True, index=True)
    notion_task_id: int | None = fields.IntField(null=True)
    title: str = fields.TextField()
    project: str | None = fields.CharField(max_length=128, null=True)
    status: str | None = fields.CharField(max_length=128, null=True)
    url: str = fields.TextField()

    decision: str = fields.CharField(
        max_length=32,
        default=TaskDecision.PENDING.value,
        index=True,
    )

    first_seen_at: datetime = fields.DatetimeField(auto_now_add=True)
    last_seen_at: datetime = fields.DatetimeField(auto_now=True)
    decided_at: datetime | None = fields.DatetimeField(null=True)
    defer_until: datetime | None = fields.DatetimeField(null=True)
    notified_at: datetime | None = fields.DatetimeField(null=True)

    class Meta:
        table = "tasks"
