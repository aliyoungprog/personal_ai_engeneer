from __future__ import annotations

from html import escape

import structlog
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ai_agent.notion.poller import NotionTask
from ai_agent.storage.state import StateStore

log = structlog.get_logger()


class TelegramService:
    def __init__(self, token: str, allowed_user_id: int, store: StateStore) -> None:
        self._bot = Bot(token=token)
        self._dp = Dispatcher()
        self._allowed_user_id = allowed_user_id
        self._store = store
        self._register_handlers()

    @property
    def bot(self) -> Bot:
        return self._bot

    @property
    def dp(self) -> Dispatcher:
        return self._dp

    def _is_allowed(self, user_id: int | None) -> bool:
        return user_id == self._allowed_user_id

    def _register_handlers(self) -> None:
        dp = self._dp

        @dp.message(Command("ping"))
        async def on_ping(msg: Message) -> None:
            if not self._is_allowed(msg.from_user.id if msg.from_user else None):
                return
            await msg.answer("pong")

        @dp.message(Command("start"))
        async def on_start(msg: Message) -> None:
            if not self._is_allowed(msg.from_user.id if msg.from_user else None):
                await msg.answer("⛔️ not authorized")
                return
            await msg.answer(
                "🤖 <b>AI Agent bot</b> готов к работе.\n\n"
                "Команды:\n"
                "/ping — проверка живости\n"
                "/list — таски ожидающие решения\n"
                "/status — статус сервиса\n",
                parse_mode="HTML",
            )

        @dp.message(Command("status"))
        async def on_status(msg: Message) -> None:
            if not self._is_allowed(msg.from_user.id if msg.from_user else None):
                return
            pending = await self._store.list_by_decision("pending")
            accepted = await self._store.list_by_decision("accepted")
            skipped = await self._store.list_by_decision("skipped")
            await msg.answer(
                f"📊 <b>Статус</b>\n"
                f"Pending: {len(pending)}\n"
                f"Accepted: {len(accepted)}\n"
                f"Skipped: {len(skipped)}\n",
                parse_mode="HTML",
            )

        @dp.message(Command("list"))
        async def on_list(msg: Message) -> None:
            if not self._is_allowed(msg.from_user.id if msg.from_user else None):
                return
            pending = await self._store.list_by_decision("pending")
            if not pending:
                await msg.answer("Нет тасок ожидающих решения.")
                return
            lines = [f"📋 <b>Pending tasks ({len(pending)}):</b>"]
            for t in pending[:20]:
                tid = f"T-{t.task_id}" if t.task_id else "?"
                lines.append(f"• <b>{tid}</b> [{escape(t.status or '?')}] {escape(t.title)}")
            await msg.answer("\n".join(lines), parse_mode="HTML")

        @dp.callback_query(F.data.startswith("task:"))
        async def on_task_decision(cb: CallbackQuery) -> None:
            if not self._is_allowed(cb.from_user.id if cb.from_user else None):
                await cb.answer("not authorized", show_alert=True)
                return
            try:
                _, action, page_id = (cb.data or "").split(":", 2)
            except ValueError:
                await cb.answer("bad callback", show_alert=True)
                return
            row = await self._store.get(page_id)
            if row is None:
                await cb.answer("task not found in store", show_alert=True)
                return
            if action == "accept":
                await self._store.set_decision(page_id, "accepted")
                await cb.answer("✅ принято в работу")
                if cb.message:
                    await cb.message.edit_text(
                        (cb.message.text or "") + "\n\n✅ <b>Принято в работу</b>",
                        parse_mode="HTML",
                    )
            elif action == "skip":
                await self._store.set_decision(page_id, "skipped")
                await cb.answer("⏭ пропущено")
                if cb.message:
                    await cb.message.edit_text(
                        (cb.message.text or "") + "\n\n⏭ <b>Пропущено</b>",
                        parse_mode="HTML",
                    )
            elif action == "defer":
                await self._store.set_decision(page_id, "deferred")
                await cb.answer("⏰ отложено")
                if cb.message:
                    await cb.message.edit_text(
                        (cb.message.text or "") + "\n\n⏰ <b>Отложено</b>",
                        parse_mode="HTML",
                    )
            else:
                await cb.answer("unknown action", show_alert=True)

    async def notify_new_task(self, task: NotionTask) -> None:
        tid = f"T-{task.task_id}" if task.task_id else "?"
        text = (
            f"🆕 <b>Новая таска {tid}</b>\n"
            f"📋 {escape(task.title)}\n"
            f"🏷 {escape(task.project or '?')} · {escape(task.status or '?')}\n"
            f"🔗 <a href=\"{task.url}\">открыть в Notion</a>"
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
        await self._bot.send_message(
            chat_id=self._allowed_user_id,
            text=text,
            parse_mode="HTML",
            reply_markup=kb,
            disable_web_page_preview=True,
        )

    async def start(self) -> None:
        log.info("telegram.start_polling")
        await self._dp.start_polling(self._bot, handle_signals=False)

    async def close(self) -> None:
        await self._bot.session.close()
