from html import escape

from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ai_agent.clients import TelegramClient
from ai_agent.database.models import TaskDecision
from ai_agent.repositories import TasksRepository
from ai_agent.services import TaskDecisionService


def register_handlers(
    dp: Dispatcher,
    tg: TelegramClient,
    tasks_repository: TasksRepository,
    decision_service: TaskDecisionService,
) -> None:
    def _allowed(message_or_cb: Message | CallbackQuery) -> bool:
        user = message_or_cb.from_user
        return tg.is_allowed(user.id if user else None)

    @dp.message(Command("ping"))
    async def on_ping(msg: Message) -> None:
        if not _allowed(msg):
            return
        await msg.answer("pong")

    @dp.message(Command("start"))
    async def on_start(msg: Message) -> None:
        if not _allowed(msg):
            await msg.answer("⛔️ not authorized")
            return
        await msg.answer(
            "🤖 <b>AI Agent bot</b> готов к работе.\n\n"
            "Команды:\n"
            "/ping — проверка живости\n"
            "/list — таски ожидающие решения\n"
            "/status — статус сервиса",
            parse_mode="HTML",
        )

    @dp.message(Command("status"))
    async def on_status(msg: Message) -> None:
        if not _allowed(msg):
            return
        counts = await tasks_repository.count_by_decision()
        lines = ["📊 <b>Статус</b>"]
        for k, v in sorted(counts.items()):
            lines.append(f"{k}: {v}")
        await msg.answer("\n".join(lines), parse_mode="HTML")

    @dp.message(Command("list"))
    async def on_list(msg: Message) -> None:
        if not _allowed(msg):
            return
        pending = await tasks_repository.list_by_decision(TaskDecision.PENDING)
        if not pending:
            await msg.answer("Нет тасок ожидающих решения.")
            return
        lines = [f"📋 <b>Pending tasks ({len(pending)}):</b>"]
        for t in pending[:20]:
            tid = f"T-{t.notion_task_id}" if t.notion_task_id else "?"
            lines.append(f"• <b>{tid}</b> [{escape(t.status or '?')}] {escape(t.title)}")
        await msg.answer("\n".join(lines), parse_mode="HTML")

    @dp.callback_query(F.data.startswith("task:"))
    async def on_decision(cb: CallbackQuery) -> None:
        if not _allowed(cb):
            await cb.answer("not authorized", show_alert=True)
            return
        try:
            _, action, page_id = (cb.data or "").split(":", 2)
        except ValueError:
            await cb.answer("bad callback", show_alert=True)
            return

        existing = await tasks_repository.get_by_page_id(page_id)
        if existing is None:
            await cb.answer("task not found", show_alert=True)
            return

        suffix_map = {
            "accept": ("\n\n✅ <b>Принято в работу</b>", "✅ принято", decision_service.accept),
            "skip": ("\n\n⏭ <b>Пропущено</b>", "⏭ пропущено", decision_service.skip),
            "defer": ("\n\n⏰ <b>Отложено</b>", "⏰ отложено", decision_service.defer),
        }
        if action not in suffix_map:
            await cb.answer("unknown action", show_alert=True)
            return

        edit_suffix, popup, handler = suffix_map[action]
        await handler(page_id)
        await cb.answer(popup)
        if cb.message:
            await cb.message.edit_text(
                (cb.message.text or "") + edit_suffix,
                parse_mode="HTML",
            )
