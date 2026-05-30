from typing import Literal

from pydantic import BaseModel

PipelineState = Literal[
    "created",
    "waiting_for_resource",
    "preparing",
    "pending",
    "running",
    "success",
    "failed",
    "canceled",
    "skipped",
    "manual",
    "scheduled",
    "no_pipeline",
]

MRState = Literal["opened", "closed", "locked", "merged"]


class MRCreateRequest(BaseModel):
    source_branch: str
    target_branch: str
    title: str
    description: str = ""
    remove_source_branch: bool = True
    squash: bool = False


class MRInfo(BaseModel):
    iid: int
    title: str
    state: MRState
    web_url: str
    source_branch: str
    target_branch: str
    has_conflicts: bool
    merge_status: str
    pipeline_state: PipelineState | None = None


class PipelineInfo(BaseModel):
    id: int | None
    state: PipelineState
    web_url: str | None = None
    ref: str
