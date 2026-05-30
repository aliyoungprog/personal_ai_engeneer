from html import escape
from uuid import UUID

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
        group_order = (
            ("pending", "⏳ Pending"),
            ("accepted", "▶️ Accepted"),
            ("deferred", "⏰ Deferred"),
            ("skipped", "⏭ Skipped"),
        )
        groups: dict[str, list] = {}
        for decision_value, _label in group_order:
            groups[decision_value] = await tasks_repository.list_by_decision(
                TaskDecision(decision_value)
            )
        total = sum(len(v) for v in groups.values())
        if total == 0:
            await msg.answer("Список пуст.")
            return

        per_group_limit = 20
        lines: list[str] = [f"📋 <b>Tasks ({total}):</b>"]
        for decision_value, label in group_order:
            items = groups[decision_value]
            if not items:
                continue
            lines.append("")
            lines.append(f"<b>{label} — {len(items)}</b>")
            for t in items[:per_group_limit]:
                tid = f"T-{t.notion_task_id}" if t.notion_task_id else "?"
                lines.append(
                    f"• <b>{tid}</b> [{escape(t.status or '?')}] {escape(t.title)}"
                )
            if len(items) > per_group_limit:
                lines.append(f"  …и ещё {len(items) - per_group_limit}")
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

    @dp.callback_query(F.data.startswith("mr:"))
    async def on_mr_decision(cb: CallbackQuery) -> None:
        if not _allowed(cb):
            await cb.answer("not authorized", show_alert=True)
            return
        try:
            _, action, run_id_str = (cb.data or "").split(":", 2)
            run_id = UUID(run_id_str)
        except (ValueError, IndexError):
            await cb.answer("bad callback", show_alert=True)
            return

        if action == "approve":
            await cb.answer("merging…")
            if cb.message:
                await cb.message.edit_text(
                    (cb.message.text or "") + "\n\n⏳ <b>Мержу...</b>",
                    parse_mode="HTML",
                )
            await decision_service.approve_mr(run_id)
        elif action == "cancel":
            await cb.answer("cancelling…")
            if cb.message:
                await cb.message.edit_text(
                    (cb.message.text or "") + "\n\n🚫 <b>Отменено</b>",
                    parse_mode="HTML",
                )
            await decision_service.cancel_run(run_id)
        else:
            await cb.answer("unknown action", show_alert=True)
