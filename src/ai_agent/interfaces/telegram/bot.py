from aiogram import Dispatcher
from loguru import logger

from ai_agent.clients import TelegramClient
from ai_agent.interfaces.telegram.handlers import register_handlers
from ai_agent.repositories import TasksRepository
from ai_agent.services import TaskDecisionService


class TelegramInterface:
    """Owns the aiogram Dispatcher and bot polling lifecycle."""

    def __init__(
        self,
        telegram_client: TelegramClient,
        tasks_repository: TasksRepository,
        decision_service: TaskDecisionService,
    ) -> None:
        self._tg = telegram_client
        self._dp = Dispatcher()
        register_handlers(
            dp=self._dp,
            tg=telegram_client,
            tasks_repository=tasks_repository,
            decision_service=decision_service,
        )

    async def start(self) -> None:
        logger.info("telegram.start_polling")
        await self._dp.start_polling(self._tg.bot, handle_signals=False)

    async def stop(self) -> None:
        await self._dp.stop_polling()
