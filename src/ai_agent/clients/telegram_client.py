import re
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from loguru import logger

from ai_agent.database.models import Task, TaskRun
from ai_agent.errors import TelegramError
from ai_agent.schemas import MRInfo, NotionTaskDTO, TaskChangesDTO

# Telegram hard-caps a message at 4096 chars; leave room for the card chrome.
_MR_BODY_LIMIT = 3500


def _inline_md_to_html(s: str) -> str:
    """Convert inline markdown (links, code, bold) in an already-escaped string."""

    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)


def _md_to_tg_html(md: str) -> str:
    """Render the markdown subset build_mr_description emits as Telegram HTML.

    Headings become bold, list items become bullets, links/code/bold are
    converted inline. Text is escaped first, then truncated to Telegram's limit.
    """

    out: list[str] = []
    for raw in md.splitlines():
        if raw.strip() == "---":
            out.append("➖➖➖")
            continue
        heading = re.match(r"^#{1,6}\s+(.*)$", raw)
        if heading:
            out.append(f"<b>{_inline_md_to_html(escape(heading.group(1)))}</b>")
            continue
        bullet = re.match(r"^(\s*)-\s+(.*)$", raw)
        if bullet:
            indent = "  " * (len(bullet.group(1)) // 2)
            out.append(f"{indent}• {_inline_md_to_html(escape(bullet.group(2)))}")
            continue
        out.append(_inline_md_to_html(escape(raw)))
    text = "\n".join(out)
    if len(text) > _MR_BODY_LIMIT:
        text = text[:_MR_BODY_LIMIT].rstrip() + "\n…"
    return text


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

    async def send_auto_taken(self, task: NotionTaskDTO) -> None:
        """Notify that the agent auto-accepted a task (autonomous mode)."""

        tid = f"T-{task.task_id}" if task.task_id else "?"
        text = (
            f"🤖 <b>Авто-взял {tid}</b> в работу\n"
            f"📋 {escape(task.title)}\n"
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
            raise TelegramError("send_auto_taken failed", details={"error": str(e)}) from e

    async def send_pending_task_card(self, task: Task) -> None:
        """Actionable card for a pending task (used by /list to take on demand)."""

        tid = f"T-{task.notion_task_id}" if task.notion_task_id else "?"
        text = (
            f"📋 <b>{tid}</b> {escape(task.title)}\n"
            f"🏷 {escape(task.project or '?')} · {escape(task.status or '?')}\n"
            f'🔗 <a href="{task.url}">открыть в Notion</a>'
        )
        pid = task.notion_page_id
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
            raise TelegramError("send_pending_task_card failed", details={"error": str(e)}) from e

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

    async def send_live(self, text: str) -> int | None:
        """Send a message meant to be edited in place; return its id.

        Best-effort: live streaming must never abort a run, so failures are
        swallowed (returns None) rather than raised.
        """

        try:
            msg = await self._bot.send_message(
                chat_id=self._allowed_user_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return msg.message_id
        except Exception as e:
            logger.warning("telegram.send_live_failed err={e}", e=str(e))
            return None

    async def edit_live(self, message_id: int, text: str) -> None:
        """Edit a previously sent live message. Best-effort, never raises.

        Telegram raises TelegramBadRequest('message is not modified') when the
        text is unchanged — that is benign and ignored.
        """

        try:
            await self._bot.edit_message_text(
                chat_id=self._allowed_user_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except TelegramBadRequest:
            pass
        except Exception as e:
            logger.warning("telegram.edit_live_failed err={e}", e=str(e))

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

    async def send_execution_failed(
        self, task: Task, run: TaskRun, reason: str, *, transient: bool = False
    ) -> None:
        tid = f"T-{task.notion_task_id}" if task.notion_task_id else "?"
        lines = [
            f"❌ <b>{tid} провалилась</b>",
            f"📋 {escape(task.title)}",
            f"Этап: <code>{escape(run.status)}</code>",
        ]
        if transient:
            lines.append(
                "🌐 Похоже, нет доступа к репозиторию (VPN?). Задача возвращена в "
                "очередь — пришлю свежую карточку, когда источник будет доступен."
            )
        lines.append(f"<pre>{escape(reason[:600])}</pre>")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔁 Повторить",
                        callback_data=f"task:accept:{task.notion_page_id}",
                    )
                ]
            ]
        )
        try:
            await self._bot.send_message(
                chat_id=self._allowed_user_id,
                text="\n".join(lines),
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        except Exception as e:
            raise TelegramError("send_execution_failed failed", details={"error": str(e)}) from e

    async def send_run_controls(self, task: Task, run: TaskRun) -> None:
        """Send a persistent ❌ Cancel control for an in-flight run."""

        tid = f"T-{task.notion_task_id}" if task.notion_task_id else "?"
        cancel_btn = InlineKeyboardButton(
            text="❌ Отменить прогон", callback_data=f"mr:cancel:{run.id}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[cancel_btn]])
        try:
            await self._bot.send_message(
                chat_id=self._allowed_user_id,
                text=f"🟢 <b>{tid}</b> запущена — {escape(task.title[:60])}",
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        except Exception as e:
            raise TelegramError("send_run_controls failed", details={"error": str(e)}) from e

    async def send_mr_ready(
        self, task: Task, run: TaskRun, mr: MRInfo, description: str | None = None
    ) -> None:
        tid = f"T-{task.notion_task_id}" if task.notion_task_id else "?"
        head = (
            f"✅ <b>{tid} готова к ревью</b> — "
            f"<a href=\"{mr.web_url}\">MR !{mr.iid}</a>"
        )
        body = f"\n\n{_md_to_tg_html(description)}" if description else f"\n📋 {escape(task.title)}"
        text = f"{head}{body}\n\nЖми <b>Approve</b> чтобы смержить."
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
