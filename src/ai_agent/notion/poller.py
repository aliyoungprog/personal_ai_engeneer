from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from notion_client import AsyncClient

log = structlog.get_logger()


@dataclass(slots=True)
class NotionTask:
    page_id: str
    task_id: int | None
    title: str
    project: str | None
    status: str | None
    url: str
    raw: dict[str, Any]


class NotionPoller:
    def __init__(
        self,
        token: str,
        data_source_id: str,
        assignee_name: str,
        projects: list[str],
        statuses: list[str],
    ) -> None:
        self._client = AsyncClient(auth=token)
        self._data_source_id = data_source_id
        self._assignee_name = assignee_name
        self._projects = projects
        self._statuses = statuses
        self._user_id: str | None = None

    async def resolve_user_id(self) -> str:
        if self._user_id is not None:
            return self._user_id
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            resp = await self._client.users.list(**params)
            for user in resp.get("results", []):
                if user.get("name") == self._assignee_name:
                    self._user_id = user["id"]
                    log.info("notion.user_resolved", name=self._assignee_name, id=self._user_id)
                    return self._user_id
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        raise RuntimeError(
            f"Notion user '{self._assignee_name}' not found. "
            "Make sure the integration is shared with the workspace and has user-read permission."
        )

    def _build_filter(self, user_id: str) -> dict[str, Any]:
        project_or = {
            "or": [
                {"property": "Project", "select": {"equals": p}} for p in self._projects
            ]
        }
        status_or = {
            "or": [
                {"property": "Status", "status": {"equals": s}} for s in self._statuses
            ]
        }
        return {
            "and": [
                {"property": "Assignee", "people": {"contains": user_id}},
                project_or,
                status_or,
            ]
        }

    async def fetch_tasks(self) -> list[NotionTask]:
        user_id = await self.resolve_user_id()
        filter_obj = self._build_filter(user_id)
        tasks: list[NotionTask] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "filter": filter_obj,
                "page_size": 100,
            }
            if cursor:
                params["start_cursor"] = cursor
            resp = await self._client.data_sources.query(
                data_source_id=self._data_source_id,
                **params,
            )
            for page in resp.get("results", []):
                tasks.append(_parse_page(page))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return tasks

    async def close(self) -> None:
        await self._client.aclose()


def _parse_page(page: dict[str, Any]) -> NotionTask:
    props = page.get("properties", {})
    title = _get_title(props.get("Task Name"))
    project = _get_select(props.get("Project"))
    status = _get_status(props.get("Status"))
    task_id = _get_number(props.get("ID"))
    return NotionTask(
        page_id=page["id"],
        task_id=task_id,
        title=title or "(untitled)",
        project=project,
        status=status,
        url=page.get("url", ""),
        raw=page,
    )


def _get_title(prop: dict[str, Any] | None) -> str | None:
    if not prop or prop.get("type") != "title":
        return None
    parts = prop.get("title", [])
    return "".join(p.get("plain_text", "") for p in parts) or None


def _get_select(prop: dict[str, Any] | None) -> str | None:
    if not prop or prop.get("type") != "select":
        return None
    sel = prop.get("select")
    return sel.get("name") if sel else None


def _get_status(prop: dict[str, Any] | None) -> str | None:
    if not prop or prop.get("type") != "status":
        return None
    st = prop.get("status")
    return st.get("name") if st else None


def _get_number(prop: dict[str, Any] | None) -> int | None:
    if not prop:
        return None
    t = prop.get("type")
    if t == "unique_id":
        uid = prop.get("unique_id", {})
        return uid.get("number")
    if t == "number":
        return prop.get("number")
    return None


