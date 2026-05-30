"""Thin GitLab API wrapper.

Wraps the sync `python-gitlab` library calls with `asyncio.to_thread`.
Exposes only what the orchestrator needs: create MR, fetch MR, fetch
latest pipeline, merge, comment.
"""

from __future__ import annotations

import asyncio

import gitlab
from gitlab.exceptions import GitlabError
from loguru import logger

from ai_agent.errors import GitLabError
from ai_agent.schemas import MRCreateRequest, MRInfo, PipelineInfo


class GitLabClient:
    def __init__(self, base_url: str, token: str, project_path: str) -> None:
        self._gl = gitlab.Gitlab(url=base_url, private_token=token)
        self._project_path = project_path
        self._project: gitlab.v4.objects.Project | None = None

    def _get_project(self) -> gitlab.v4.objects.Project:
        if self._project is None:
            try:
                self._project = self._gl.projects.get(self._project_path)
            except GitlabError as e:
                raise GitLabError(
                    "fetch project failed",
                    details={"project": self._project_path, "error": str(e)},
                ) from e
        return self._project

    async def create_merge_request(self, request: MRCreateRequest) -> MRInfo:
        def _sync() -> MRInfo:
            project = self._get_project()
            try:
                mr = project.mergerequests.create(
                    {
                        "source_branch": request.source_branch,
                        "target_branch": request.target_branch,
                        "title": request.title,
                        "description": request.description,
                        "remove_source_branch": request.remove_source_branch,
                        "squash": request.squash,
                    }
                )
            except GitlabError as e:
                raise GitLabError(
                    "create MR failed",
                    details={"source": request.source_branch, "error": str(e)},
                ) from e
            return _mr_to_info(mr)

        logger.info(
            "gitlab.create_mr source={s} target={t} title={ti}",
            s=request.source_branch,
            t=request.target_branch,
            ti=request.title[:80],
        )
        return await asyncio.to_thread(_sync)

    async def get_merge_request(self, iid: int) -> MRInfo:
        def _sync() -> MRInfo:
            project = self._get_project()
            try:
                mr = project.mergerequests.get(iid)
            except GitlabError as e:
                raise GitLabError(
                    "fetch MR failed",
                    details={"iid": iid, "error": str(e)},
                ) from e
            return _mr_to_info(mr)

        return await asyncio.to_thread(_sync)

    async def merge_merge_request(self, iid: int, *, squash: bool = False) -> MRInfo:
        def _sync() -> MRInfo:
            project = self._get_project()
            try:
                mr = project.mergerequests.get(iid)
                mr.merge(
                    should_remove_source_branch=True,
                    squash=squash,
                    merge_when_pipeline_succeeds=False,
                )
                mr = project.mergerequests.get(iid)
            except GitlabError as e:
                raise GitLabError(
                    "merge MR failed",
                    details={"iid": iid, "error": str(e)},
                ) from e
            return _mr_to_info(mr)

        logger.info("gitlab.merge iid={i} squash={s}", i=iid, s=squash)
        return await asyncio.to_thread(_sync)

    async def get_latest_pipeline(self, ref: str) -> PipelineInfo:
        def _sync() -> PipelineInfo:
            project = self._get_project()
            try:
                pipelines = project.pipelines.list(
                    ref=ref,
                    per_page=1,
                    order_by="id",
                    sort="desc",
                    get_all=False,
                )
            except GitlabError as e:
                raise GitLabError(
                    "list pipelines failed",
                    details={"ref": ref, "error": str(e)},
                ) from e
            if not pipelines:
                return PipelineInfo(id=None, state="no_pipeline", web_url=None, ref=ref)
            p = pipelines[0]
            return PipelineInfo(
                id=p.id,
                state=p.status,
                web_url=p.web_url,
                ref=ref,
            )

        return await asyncio.to_thread(_sync)

    async def post_note(self, iid: int, body: str) -> None:
        def _sync() -> None:
            project = self._get_project()
            try:
                mr = project.mergerequests.get(iid)
                mr.notes.create({"body": body})
            except GitlabError as e:
                raise GitLabError(
                    "post MR note failed",
                    details={"iid": iid, "error": str(e)},
                ) from e

        await asyncio.to_thread(_sync)


def _mr_to_info(mr: gitlab.v4.objects.ProjectMergeRequest) -> MRInfo:
    return MRInfo(
        iid=mr.iid,
        title=mr.title,
        state=mr.state,
        web_url=mr.web_url,
        source_branch=mr.source_branch,
        target_branch=mr.target_branch,
        has_conflicts=getattr(mr, "has_conflicts", False),
        merge_status=getattr(mr, "detailed_merge_status", getattr(mr, "merge_status", "unknown")),
        pipeline_state=getattr(getattr(mr, "head_pipeline", None) or {}, "get", lambda *_: None)(
            "status"
        ),
    )
