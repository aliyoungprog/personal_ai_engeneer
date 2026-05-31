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

    # Identity stamped on the commits the agent makes. Set to the human owner so
    # the work is attributed to them (GitLab links commits to accounts by email).
    git_author_name: str = "Abduvali Abdurakhmanov"
    git_author_email: str = "abdurakhmanov.a@choco.kz"

    poll_interval_seconds: int = 300
    database_url: str = "postgres://agent:agent@postgres:5432/agent"
    log_level: str = "INFO"

    # Model aliases for the coding/review agents. Sonnet is much lighter on the
    # Max 5-hour rate limit than Opus, which matters for an always-on agent.
    coder_model: str = "sonnet"
    # Reviewer is the quality gate — run it on the strongest model so its
    # findings are precise and trustworthy, even though opus is heavier on the
    # 5-hour rate limit than sonnet.
    reviewer_model: str = "opus"

    # When true the executor stops after REVIEWING (no push / MR / merge).
    execution_dry_run: bool = False

    # Max coder passes per run. The first pass is the initial implementation;
    # each subsequent pass feeds gate/reviewer feedback back to the coder to fix.
    # The run fails only if gates/reviewer are still unhappy after this many passes.
    max_iterations: int = Field(default=3, ge=1)

    # Stream the coder's live activity (file edits, commands, short thoughts)
    # into a single, throttled Telegram message during the CODING stage.
    # Falls back to plain stage-level pings if it ever destabilises the run.
    stream_coding_to_telegram: bool = True
    # Minimum seconds between Telegram edits of the live message (rate-limit
    # safety — Telegram throttles frequent edits to the same chat).
    stream_min_interval_seconds: float = 3.0

    # Debug: on startup, inject one synthetic accepted task and run it through
    # the real execution service (PID-1 path, bypasses Telegram). For testing.
    demo_task_on_start: bool = False

    @property
    def notion_project_whitelist(self) -> list[str]:
        return _csv(self.notion_project_whitelist_raw)

    @property
    def notion_status_whitelist(self) -> list[str]:
        return _csv(self.notion_status_whitelist_raw)


settings = Settings()  # type: ignore[call-arg]
