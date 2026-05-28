from ai_agent.dependencies._dependencies import (
    get_container,
    get_notion_client,
    get_notion_poller,
    get_task_decision_service,
    get_task_intake_service,
    get_tasks_repository,
    get_telegram_client,
    get_telegram_interface,
)
from ai_agent.dependencies.di_container import DIContainer

__all__ = (
    "DIContainer",
    "get_container",
    "get_notion_client",
    "get_notion_poller",
    "get_task_decision_service",
    "get_task_intake_service",
    "get_tasks_repository",
    "get_telegram_client",
    "get_telegram_interface",
)
