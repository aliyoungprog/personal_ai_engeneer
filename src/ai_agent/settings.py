from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(min_length=10)
    telegram_allowed_user_id: int

    notion_token: str = Field(min_length=10)
    notion_data_source_id: str
    notion_assignee_name: str

    notion_project_whitelist_raw: str = Field(
        alias="NOTION_PROJECT_WHITELIST",
        default="AI Platform,AI Assistant,AI CC",
    )
    notion_status_whitelist_raw: str = Field(
        alias="NOTION_STATUS_WHITELIST",
        default="To Do,Triage",
    )

    gitlab_url: str = "https://git.chocodev.kz"
    gitlab_token: str = ""
    gitlab_project_path: str = "freedom-ai/platform/ai-agents-platform"

    poll_interval_seconds: int = 300
    database_url: str = "postgres://agent:agent@postgres:5432/agent"
    log_level: str = "INFO"

    # Model aliases for the coding/review agents. Sonnet is much lighter on the
    # Max 5-hour rate limit than Opus, which matters for an always-on agent.
    coder_model: str = "sonnet"
    reviewer_model: str = "sonnet"

    # When true the executor stops after REVIEWING (no push / MR / merge).
    execution_dry_run: bool = False

    @property
    def notion_project_whitelist(self) -> list[str]:
        return _csv(self.notion_project_whitelist_raw)

    @property
    def notion_status_whitelist(self) -> list[str]:
        return _csv(self.notion_status_whitelist_raw)


settings = Settings()  # type: ignore[call-arg]
