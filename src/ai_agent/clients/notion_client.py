from typing import Any

from loguru import logger
from notion_client import AsyncClient

from ai_agent.clients._notion_blocks import blocks_to_markdown
from ai_agent.errors import NotionError
from ai_agent.schemas import NotionTaskDTO


class NotionClient:
    """Thin wrapper around `notion_client.AsyncClient` exposing only what we need."""

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

    async def close(self) -> None:
        await self._client.aclose()

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
                    logger.info(
                        "notion.user_resolved name={name} id={uid}",
                        name=self._assignee_name,
                        uid=self._user_id,
                    )
                    return self._user_id
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        raise NotionError(
            "Notion user not found",
            details={"name": self._assignee_name},
        )

    def _build_filter(self, user_id: str) -> dict[str, Any]:
        project_or = {
            "or": [{"property": "Project", "select": {"equals": p}} for p in self._projects]
        }
        status_or = {
            "or": [{"property": "Status", "status": {"equals": s}} for s in self._statuses]
        }
        return {
            "and": [
                {"property": "Assignee", "people": {"contains": user_id}},
                project_or,
                status_or,
            ]
        }

    async def fetch_tasks(self) -> list[NotionTaskDTO]:
        user_id = await self.resolve_user_id()
        filter_obj = self._build_filter(user_id)
        results: list[NotionTaskDTO] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"filter": filter_obj, "page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            resp = await self._client.data_sources.query(
                data_source_id=self._data_source_id,
                **params,
            )
            for page in resp.get("results", []):
                results.append(_parse_page(page))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return results

    async def fetch_page_body(self, page_id: str, max_depth: int = 2) -> str:
        """Fetch a page's block tree and render it to Markdown.

        Recurses into child blocks up to `max_depth` to capture nested lists
        and toggles. Returns an empty string on failure (body is best-effort).
        """

        try:
            blocks = await self._fetch_blocks(page_id, depth=0, max_depth=max_depth)
        except Exception as e:
            logger.warning("notion.fetch_body_failed page={p} err={e}", p=page_id, e=str(e))
            return ""
        return blocks_to_markdown(blocks)

    async def _fetch_blocks(
        self,
        block_id: str,
        depth: int,
        max_depth: int,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"block_id": block_id, "page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            resp = await self._client.blocks.children.list(**params)
            for block in resp.get("results", []):
                if block.get("has_children") and depth < max_depth:
                    block["_children"] = await self._fetch_blocks(
                        block["id"],
                        depth=depth + 1,
                        max_depth=max_depth,
                    )
                collected.append(block)
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return collected


def _parse_page(page: dict[str, Any]) -> NotionTaskDTO:
    props = page.get("properties", {})
    return NotionTaskDTO(
        page_id=page["id"],
        task_id=_get_number(props.get("ID")),
        title=_get_title(props.get("Task Name")) or "(untitled)",
        project=_get_select(props.get("Project")),
        status=_get_status(props.get("Status")),
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
        return prop.get("unique_id", {}).get("number")
    if t == "number":
        return prop.get("number")
    return None
