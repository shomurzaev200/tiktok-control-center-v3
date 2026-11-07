import asyncio
import html
import signal
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func

from app.config import settings
from app.database import AsyncSessionLocal, redis_client
from app.logger import log_event
from app.models import TikTokAccount, QueueTask, TargetVideo, SystemLog, NotificationLog, SystemSetting
from app.schema_bootstrap import ensure_schema
from app.services.monitoring_service import monitoring_service

dp = Dispatcher()
stop_event = asyncio.Event()


def is_valid_bot_token(token: str) -> bool:
    if not token or "your_telegram_bot_token" in token or ":" not in token:
        return False
    parts = token.split(":", 1)
    return parts[0].isdigit() and len(parts[1]) >= 20


def is_authorized_owner(user_id: int | None) -> bool:
    return bool(settings.OWNER_TELEGRAM_ID and user_id == settings.OWNER_TELEGRAM_ID)


def build_main_keyboard(paused: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Состояние сервисов", callback_data="cb_services"),
                InlineKeyboardButton(text="⚡ Очередь и DLQ", callback_data="cb_queue"),
            ],
            [
                InlineKeyboardButton(text="👥 Аккаунты", callback_data="cb_accounts"),
                InlineKeyboardButton(text="❌ Последние ошибки", callback_data="cb_errors"),
            ],
            [
                InlineKeyboardButton(
                    text="▶️ Возобновить очередь" if paused else "⏸ Поставить очередь на паузу",
                    callback_data="cb_toggle_ask",
                ),
            ],
            [
                InlineKeyboardButton(text="🔔 История уведомлений", callback_data="cb_notif_hist"),
            ],
        ]
    )


async def build_overview_text() -> tuple[str, bool]:
    metrics = await monitoring_service.collect_system_metrics()
    paused = await redis_client.get("queue:paused") == "1"
    async with AsyncSessionLocal() as db:
        acc_count = await db.scalar(select(func.count(TikTokAccount.id))) or 0
        vid_count = await db.scalar(select(func.count(TargetVideo.id))) or 0
        pending = await db.scalar(select(func.count(QueueTask.id)).where(QueueTask.status == "pending")) or 0
        verif = await db.scalar(select(func.count(QueueTask.id)).where(QueueTask.status == "verification_required")) or 0
        done = await db.scalar(select(func.count(QueueTask.id)).where(QueueTask.status.in_(["completed", "published"]))) or 0
        dlq = await db.scalar(select(func.count(QueueTask.id)).where(QueueTask.status.in_(["failed", "dlq"]))) or 0

    text = (
        f"📡 <b>TIKTOK CONTROL CENTER v3.0</b>\n\n"
        f"⚙️ Воркер: <b>{'🟢 ONLINE' if metrics['worker_online'] else '🔴 OFFLINE'}</b>\n"
        f"🗄 БД PostgreSQL: <b>{'🟢 OK' if metrics['db_online'] else '🔴 DOWN'} ({metrics['db_latency_ms']} мс)</b>\n"
        f"⚡ Redis: <b>{'🟢 OK' if metrics['redis_online'] else '🔴 DOWN'} ({metrics['redis_latency_ms']} мс)</b>\n"
        f"💻 Ресурсы: CPU <b>{metrics['cpu_percent']}%</b> | RAM своб. <b>{metrics['free_ram_mb']} МБ</b> | Диск своб. <b>{metrics['free_disk_mb']} МБ</b>\n"
        f"⏸ Очередь: <b>{'НА ПАУЗЕ' if paused else 'АКТИВНА'}</b>\n\n"
        f"👥 Аккаунтов: <b>{acc_count}</b> | 🎬 Видео: <b>{vid_count}</b>\n"
        f"⏳ В очереди: <b>{pending}</b> | 🔍 На проверке: <b>{verif}</b>\n"
        f"✅ Завершено: <b>{done}</b> | 🚨 DLQ/Ошибок: <b>{dlq}</b>"
    )
    return text, paused


@dp.message(Command("start", "status"))
async def cmd_start(message: types.Message):
    if not is_authorized_owner(message.from_user.id if message.from_user else None):
        return
    text, paused = await build_overview_text()
    await message.answer(text, parse_mode="HTML", reply_markup=build_main_keyboard(paused))


@dp.callback_query(F.data == "cb_services")
async def cb_services(call: types.CallbackQuery):
    if not is_authorized_owner(call.from_user.id):
        await call.answer("Доступ запрещён", show_alert=True)
        return
    text, paused = await build_overview_text()
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=build_main_keyboard(paused))
    await call.answer()


@dp.callback_query(F.data == "cb_queue")
async def cb_queue(call: types.CallbackQuery):
    if not is_authorized_owner(call.from_user.id):
        return
    async with AsyncSessionLocal() as db:
        tasks = (await db.scalars(select(QueueTask).order_by(QueueTask.id.desc()).limit(8))).all()
    lines = ["⚡ <b>Последние задания в очереди:</b>\n"]
    for t in tasks:
        lines.append(
            f"• <code>#{t.id}</code> [{html.escape(t.queue_name)}] статус: <b>{html.escape(t.status)}</b> "
            f"(рез: {html.escape(t.external_result)})"
        )
    if len(lines) == 1:
        lines.append("Очередь пуста.")
    paused = await redis_client.get("queue:paused") == "1"
    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=build_main_keyboard(paused))
    await call.answer()


@dp.callback_query(F.data == "cb_accounts")
async def cb_accounts(call: types.CallbackQuery):
    if not is_authorized_owner(call.from_user.id):
        return
    async with AsyncSessionLocal() as db:
        accs = (await db.scalars(select(TikTokAccount).order_by(TikTokAccount.id.asc()).limit(10))).all()
    lines = ["👥 <b>Состояние аккаунтов:</b>\n"]
    for a in accs:
        lines.append(
            f"• <code>{html.escape(a.username)}</code> — <b>{html.escape(a.status)}</b> | "
            f"сессия: {html.escape(a.auth_state)} | сегодня: {a.comments_today}/{a.daily_comment_limit}"
        )
    if len(lines) == 1:
        lines.append("Аккаунты не добавлены.")
    paused = await redis_client.get("queue:paused") == "1"
    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=build_main_keyboard(paused))
    await call.answer()


@dp.callback_query(F.data == "cb_errors")
async def cb_errors(call: types.CallbackQuery):
    if not is_authorized_owner(call.from_user.id):
        return
    async with AsyncSessionLocal() as db:
        errs = (
            await db.scalars(
                select(SystemLog).where(SystemLog.level == "ERROR").order_by(SystemLog.id.desc()).limit(5)
            )
        ).all()
    lines = ["❌ <b>Последние ошибки:</b>\n"]
    for e in errs:
        lines.append(f"• [{html.escape(e.service)}] <b>{html.escape(e.event)}</b>: {html.escape(e.message[:120])}")
    if len(lines) == 1:
        lines.append("Ошибок не зафиксировано.")
    paused = await redis_client.get("queue:paused") == "1"
    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=build_main_keyboard(paused))
    await call.answer()


@dp.callback_query(F.data == "cb_toggle_ask")
async def cb_toggle_ask(call: types.CallbackQuery):
    if not is_authorized_owner(call.from_user.id):
        return
    paused = await redis_client.get("queue:paused") == "1"
    action_label = "возобновить обработку очереди" if paused else "поставить очередь на паузу"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="cb_toggle_confirm"),
                InlineKeyboardButton(text="🔙 Отмена", callback_data="cb_services"),
            ]
        ]
    )
    await call.message.edit_text(
        f"⚠️ Вы уверены, что хотите <b>{action_label}</b>?", parse_mode="HTML", reply_markup=kb
    )
    await call.answer()


@dp.callback_query(F.data == "cb_toggle_confirm")
async def cb_toggle_confirm(call: types.CallbackQuery):
    if not is_authorized_owner(call.from_user.id):
        return
    paused = await redis_client.get("queue:paused") == "1"
    if paused:
        await redis_client.delete("queue:paused")
    else:
        await redis_client.set("queue:paused", "1")
    text, new_paused = await build_overview_text()
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=build_main_keyboard(new_paused))
    await call.answer("Статус очереди обновлён")


@dp.callback_query(F.data == "cb_notif_hist")
async def cb_notif_hist(call: types.CallbackQuery):
    if not is_authorized_owner(call.from_user.id):
        return
    async with AsyncSessionLocal() as db:
        rows = (await db.scalars(select(NotificationLog).order_by(NotificationLog.id.desc()).limit(6))).all()
    lines = ["🔔 <b>Последние уведомления:</b>\n"]
    for r in rows:
        lines.append(f"• [{html.escape(r.status)}] <b>{html.escape(r.title)}</b>")
    if len(lines) == 1:
        lines.append("История уведомлений пуста.")
    paused = await redis_client.get("queue:paused") == "1"
    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=build_main_keyboard(paused))
    await call.answer()


async def dispatch_notifications_loop(bot: Bot | None):
    Path("/tmp/tg_healthy").touch()
    prev_worker_online = True
    while not stop_event.is_set():
        try:
            await redis_client.set("telegram:heartbeat", "online", ex=60)
            Path("/tmp/tg_healthy").touch()

            worker_hb = await redis_client.get("worker:heartbeat")
            is_online = bool(worker_hb)
            if prev_worker_online and not is_online:
                async with AsyncSessionLocal() as db:
                    db.add(
                        NotificationLog(
                            category="alert",
                            title="🚨 Воркер перестал отвечать (OFFLINE)",
                            message="Heartbeat воркера истёк. Проверьте состояние контейнера tt_v3_worker.",
                            status="pending",
                        )
                    )
                    await db.commit()
            elif not prev_worker_online and is_online:
                async with AsyncSessionLocal() as db:
                    db.add(
                        NotificationLog(
                            category="recovery",
                            title="🟢 Работа воркера восстановлена (ONLINE)",
                            message="Heartbeat воркера снова активен.",
                            status="pending",
                        )
                    )
                    await db.commit()
            prev_worker_online = is_online

            if bot and settings.OWNER_TELEGRAM_ID:
                async with AsyncSessionLocal() as db:
                    pending_list = (
                        await db.scalars(
                            select(NotificationLog)
                            .where(NotificationLog.status == "pending", NotificationLog.attempts < 5)
                            .order_by(NotificationLog.id.asc())
                            .limit(10)
                        )
                    ).all()
                    for notif in pending_list:
                        notif.attempts += 1
                        safe_title = html.escape(notif.title)
                        safe_body = html.escape(notif.message)
                        try:
                            await bot.send_message(
                                chat_id=settings.OWNER_TELEGRAM_ID,
                                text=f"<b>{safe_title}</b>\n\n{safe_body}",
                                parse_mode="HTML",
                                disable_web_page_preview=True,
                            )
                            notif.status = "sent"
                            notif.sent_at = datetime.now(timezone.utc)
                        except Exception as send_err:
                            notif.last_error = str(send_err)[:200]
                            if notif.attempts >= 5:
                                notif.status = "failed"
                        await db.commit()
        except Exception as exc:
            await log_event("telegram", "WARNING", "notif_loop_err", str(exc))

        await asyncio.sleep(4)


async def main():
    await ensure_schema()
    if not is_valid_bot_token(settings.TELEGRAM_BOT_TOKEN):
        await log_event(
            "telegram",
            "WARNING",
            "bot_standby",
            "TELEGRAM_BOT_TOKEN не настроен — сервис работает в режиме фонового мониторинга.",
        )
        await dispatch_notifications_loop(None)
        return

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    await log_event("telegram", "INFO", "bot_started", "Telegram Monitor v3.0 запущен")
    await asyncio.gather(
        dispatch_notifications_loop(bot),
        dp.start_polling(bot, handle_signals=False),
    )


if __name__ == "__main__":
    asyncio.run(main())
