from typing import Any

from pydantic import BaseModel, ConfigDict


class NotionTaskDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    page_id: str
    task_id: int | None = None
    title: str
    project: str | None = None
    status: str | None = None
    url: str
    raw: dict[str, Any]
