from dependency_injector import containers, providers

from ai_agent.clients import NotionClient, TelegramClient
from ai_agent.interfaces.notion_poller import NotionPollerInterface
from ai_agent.interfaces.telegram import TelegramInterface
from ai_agent.repositories import TasksRepository
from ai_agent.services import TaskDecisionService, TaskExecutionService, TaskIntakeService
from ai_agent.settings import settings


class DIContainer(containers.DeclarativeContainer):
    app_settings = providers.Object(settings)

    # Repositories
    tasks_repository = providers.Singleton(TasksRepository)

    # Clients
    notion_client = providers.Singleton(
        NotionClient,
        token=app_settings.provided.notion_token,
        data_source_id=app_settings.provided.notion_data_source_id,
        assignee_name=app_settings.provided.notion_assignee_name,
        projects=app_settings.provided.notion_project_whitelist,
        statuses=app_settings.provided.notion_status_whitelist,
    )
    telegram_client = providers.Singleton(
        TelegramClient,
        bot_token=app_settings.provided.telegram_bot_token,
        allowed_user_id=app_settings.provided.telegram_allowed_user_id,
    )

    # Services
    task_execution_service = providers.Singleton(TaskExecutionService)
    task_decision_service = providers.Singleton(
        TaskDecisionService,
        tasks_repository=tasks_repository,
        execution_service=task_execution_service,
    )
    task_intake_service = providers.Singleton(
        TaskIntakeService,
        tasks_repository=tasks_repository,
        telegram_client=telegram_client,
    )

    # Interfaces
    telegram_interface = providers.Singleton(
        TelegramInterface,
        telegram_client=telegram_client,
        tasks_repository=tasks_repository,
        decision_service=task_decision_service,
    )
    notion_poller_interface = providers.Singleton(
        NotionPollerInterface,
        notion_client=notion_client,
        intake_service=task_intake_service,
        interval_seconds=app_settings.provided.poll_interval_seconds,
    )
