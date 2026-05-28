from html import escape

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ai_agent.database.models import Task
from ai_agent.errors import TelegramError
from ai_agent.schemas import NotionTaskDTO, TaskChangesDTO


class TelegramClient:
    """Thin wrapper for outbound Telegram messages.

    Inbound dispatcher and handlers live in `interfaces/telegram/`.
    """

    def __init__(self, bot_token: str, allowed_user_id: int) -> None:
        self._bot = Bot(token=bot_token)
        self._allowed_user_id = allowed_user_id

    @property
    def bot(self) -> Bot:
        return self._bot

    @property
    def allowed_user_id(self) -> int:
        return self._allowed_user_id

    def is_allowed(self, user_id: int | None) -> bool:
        return user_id == self._allowed_user_id

    async def close(self) -> None:
        await self._bot.session.close()

    async def send_new_task_card(self, task: NotionTaskDTO) -> None:
        tid = f"T-{task.task_id}" if task.task_id else "?"
        text = (
            f"🆕 <b>Новая таска {tid}</b>\n"
            f"📋 {escape(task.title)}\n"
            f"🏷 {escape(task.project or '?')} · {escape(task.status or '?')}\n"
            f'🔗 <a href="{task.url}">открыть в Notion</a>'
        )
        pid = task.page_id
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="▶️ Взять", callback_data=f"task:accept:{pid}"),
                    InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"task:skip:{pid}"),
                    InlineKeyboardButton(text="⏰ Позже", callback_data=f"task:defer:{pid}"),
                ]
            ]
        )
        try:
            await self._bot.send_message(
                chat_id=self._allowed_user_id,
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        except Exception as e:
            raise TelegramError("send_new_task_card failed", details={"error": str(e)}) from e

    async def send_task_changed(self, task: NotionTaskDTO, changes: TaskChangesDTO) -> None:
        tid = f"T-{task.task_id}" if task.task_id else "?"
        labels = {"title": "Title", "status": "Status", "project": "Project"}
        lines = [f"🔄 <b>Изменена таска {tid}</b>", f"📋 {escape(task.title)}"]
        for field, old, new in changes.iter_fields():
            lines.append(
                f"{labels.get(field, field)}: "
                f"<s>{escape(old or '—')}</s> → <b>{escape(new or '—')}</b>"
            )
        lines.append(f'🔗 <a href="{task.url}">открыть в Notion</a>')
        try:
            await self._bot.send_message(
                chat_id=self._allowed_user_id,
                text="\n".join(lines),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            raise TelegramError("send_task_changed failed", details={"error": str(e)}) from e

    async def send_task_removed(self, task: Task) -> None:
        tid = f"T-{task.notion_task_id}" if task.notion_task_id else "?"
        text = (
            f"🗑 <b>Таска {tid} ушла из очереди</b>\n"
            f"📋 {escape(task.title)}\n"
            "Сейчас её нет в фильтре (удалена / сменили статус / assignee).\n"
            f'🔗 <a href="{task.url}">открыть в Notion</a>'
        )
        try:
            await self._bot.send_message(
                chat_id=self._allowed_user_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            raise TelegramError("send_task_removed failed", details={"error": str(e)}) from e
