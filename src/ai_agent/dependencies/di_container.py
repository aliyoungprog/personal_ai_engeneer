from pathlib import Path

from dependency_injector import containers, providers

from ai_agent.ai import ClaudeRunner, ReviewerAgent
from ai_agent.clients import GitLabClient, NotionClient, TelegramClient
from ai_agent.gates import QualityGates
from ai_agent.git_ops import RepoManager, WorktreeManager
from ai_agent.interfaces.notion_poller import NotionPollerInterface
from ai_agent.interfaces.telegram import TelegramInterface
from ai_agent.repositories import TaskRunsRepository, TasksRepository
from ai_agent.services import TaskDecisionService, TaskExecutionService, TaskIntakeService
from ai_agent.settings import settings


def _repo_base_path(_settings: object = settings) -> Path:
    name = settings.gitlab_project_path.rsplit("/", 1)[-1]
    return Path("/app/repos") / name


def _https_repo_url(_settings: object = settings) -> str:
    return f"{settings.gitlab_url.rstrip('/')}/{settings.gitlab_project_path}.git"


class DIContainer(containers.DeclarativeContainer):
    app_settings = providers.Object(settings)

    # Repositories
    tasks_repository = providers.Singleton(TasksRepository)
    task_runs_repository = providers.Singleton(TaskRunsRepository)

    # External clients
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
    gitlab_client = providers.Singleton(
        GitLabClient,
        base_url=app_settings.provided.gitlab_url,
        token=app_settings.provided.gitlab_token,
        project_path=app_settings.provided.gitlab_project_path,
    )

    # Git ops
    repo_manager = providers.Singleton(
        RepoManager,
        base_path=providers.Callable(_repo_base_path),
        https_url=providers.Callable(_https_repo_url),
        token=app_settings.provided.gitlab_token,
        default_branch="test",
    )
    worktree_manager = providers.Singleton(
        WorktreeManager,
        repo=repo_manager,
        worktrees_root=providers.Object(Path("/app/worktrees")),
    )

    # AI components
    claude_runner = providers.Singleton(ClaudeRunner)
    reviewer = providers.Singleton(ReviewerAgent, runner=claude_runner)

    # Quality gates
    quality_gates = providers.Singleton(QualityGates)

    # Services
    task_execution_service = providers.Singleton(
        TaskExecutionService,
        task_runs_repository=task_runs_repository,
        claude_runner=claude_runner,
        reviewer=reviewer,
        quality_gates=quality_gates,
        repo_manager=repo_manager,
        worktree_manager=worktree_manager,
        gitlab_client=gitlab_client,
        telegram_client=telegram_client,
        notion_client=notion_client,
    )
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
