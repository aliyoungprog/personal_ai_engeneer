from typing import Any


class AgentError(Exception):
    """Base exception for ai_agent."""

    def __init__(self, message: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class DatabaseError(AgentError):
    """Raised on database operation failure."""


class NotFoundError(AgentError):
    """Raised when a requested record is not found."""


class NotionError(AgentError):
    """Raised on Notion API operation failure."""


class TelegramError(AgentError):
    """Raised on Telegram operation failure."""
