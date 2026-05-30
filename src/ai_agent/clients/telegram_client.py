from html import escape

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ai_agent.database.models import Task, TaskRun
from ai_agent.errors import TelegramError
from ai_agent.schemas import MRInfo, NotionTaskDTO, TaskChangesDTO


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

    async def send_execution_progress(self, task: Task, run: TaskRun, line: str) -> None:
        tid = f"T-{task.notion_task_id}" if task.notion_task_id else "?"
        text = f"⚙️ <b>{tid}</b> {escape(line)}"
        try:
            await self._bot.send_message(
                chat_id=self._allowed_user_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            raise TelegramError("send_execution_progress failed", details={"error": str(e)}) from e

    async def send_execution_failed(self, task: Task, run: TaskRun, reason: str) -> None:
        tid = f"T-{task.notion_task_id}" if task.notion_task_id else "?"
        text = (
            f"❌ <b>{tid} провалилась</b>\n"
            f"📋 {escape(task.title)}\n"
            f"Этап: <code>{escape(run.status)}</code>\n"
            f"<pre>{escape(reason[:600])}</pre>"
        )
        try:
            await self._bot.send_message(
                chat_id=self._allowed_user_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            raise TelegramError("send_execution_failed failed", details={"error": str(e)}) from e

    async def send_mr_ready(self, task: Task, run: TaskRun, mr: MRInfo) -> None:
        tid = f"T-{task.notion_task_id}" if task.notion_task_id else "?"
        text = (
            f"✅ <b>{tid} готова к ревью</b>\n"
            f"📋 {escape(task.title)}\n"
            f"🔗 <a href=\"{mr.web_url}\">MR !{mr.iid}</a>\n"
            f"Жми <b>Approve</b> чтобы смержить."
        )
        rid = str(run.id)
        approve_cb = f"mr:approve:{rid}"
        cancel_cb = f"mr:cancel:{rid}"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Approve & merge", callback_data=approve_cb),
                    InlineKeyboardButton(text="❌ Cancel", callback_data=cancel_cb),
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
            raise TelegramError("send_mr_ready failed", details={"error": str(e)}) from e

    async def send_run_finished(
        self,
        task: Task,
        run: TaskRun,
        mr_url: str | None,
        succeeded: bool,
    ) -> None:
        tid = f"T-{task.notion_task_id}" if task.notion_task_id else "?"
        if succeeded:
            text = (
                f"🎉 <b>{tid} смержена</b>\n"
                f"📋 {escape(task.title)}\n"
                f"🔗 <a href=\"{mr_url}\">MR</a>"
            )
        else:
            link = f'\n🔗 <a href="{mr_url}">MR</a>' if mr_url else ""
            text = (
                f"🚫 <b>{tid} отменена</b>\n"
                f"📋 {escape(task.title)}{link}"
            )
        try:
            await self._bot.send_message(
                chat_id=self._allowed_user_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            raise TelegramError("send_run_finished failed", details={"error": str(e)}) from e

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
