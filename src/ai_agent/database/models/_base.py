from datetime import datetime
from typing import TypeVar
from uuid import UUID, uuid4

from tortoise import fields, models


class BaseAbstractModel(models.Model):
    """Abstract base model with UUID primary key and standard timestamps."""

    id: UUID = fields.UUIDField(default=uuid4, pk=True)
    created_at: datetime = fields.DatetimeField(auto_now_add=True)
    updated_at: datetime = fields.DatetimeField(auto_now=True)

    class Meta:
        abstract = True


M = TypeVar("M", bound=BaseAbstractModel)
