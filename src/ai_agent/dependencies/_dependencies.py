"""Factory functions to access singleton dependencies via the DI container."""

from ai_agent.clients import NotionClient, TelegramClient
from ai_agent.dependencies.di_container import DIContainer
from ai_agent.interfaces.notion_poller import NotionPollerInterface
from ai_agent.interfaces.telegram import TelegramInterface
from ai_agent.repositories import TasksRepository
from ai_agent.services import TaskDecisionService, TaskIntakeService

_container: DIContainer | None = None


def get_container() -> DIContainer:
    global _container
    if _container is None:
        _container = DIContainer()
    return _container


def get_tasks_repository() -> TasksRepository:
    return get_container().tasks_repository()


def get_notion_client() -> NotionClient:
    return get_container().notion_client()


def get_telegram_client() -> TelegramClient:
    return get_container().telegram_client()


def get_task_intake_service() -> TaskIntakeService:
    return get_container().task_intake_service()


def get_task_decision_service() -> TaskDecisionService:
    return get_container().task_decision_service()


def get_telegram_interface() -> TelegramInterface:
    return get_container().telegram_interface()


def get_notion_poller() -> NotionPollerInterface:
    return get_container().notion_poller_interface()
