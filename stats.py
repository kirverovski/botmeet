"""
stats.py — логика сбора и отображения статистики
"""
from datetime import datetime, time
from sqlalchemy import select, func
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, Application

from db import DailyStat, User, Meeting, get_db
from config import ADMIN_USER_ID  # Вы зададите свой ID в .env

# Импортируем logger
import logging
logger = logging.getLogger(__name__)


async def increment_stat(stat_type: str):
    """
    Увеличивает счётчик на 1.
    stat_type: 'new_users' или 'new_meetings'
    """
    async with get_db() as db:
        today = datetime.now().date()
        result = await db.execute(
            select(DailyStat).where(
                func.date(DailyStat.date) == today
            )
        )
        row = result.scalar_one_or_none()

        if row:
            if stat_type == "new_users":
                row.new_users += 1
            elif stat_type == "new_meetings":
                row.new_meetings += 1
        else:
            row = DailyStat(date=datetime.now())
            if stat_type == "new_users":
                row.new_users = 1
            elif stat_type == "new_meetings":
                row.new_meetings = 1
            db.add(row)

        await db.commit()
        logger.info(f"📊 Статистика обновлена: {stat_type}")


async def send_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Отправляет статистику только администратору.
    Команда: /stats
    """
    user_id = update.effective_user.id

    # Проверка: только админ
    if str(user_id) != str(ADMIN_USER_ID):
        await update.message.reply_text("🔒 Доступ запрещён.")
        return

    async with get_db() as db:
        # Статистика за сегодня
        today = datetime.now().date()
        result = await db.execute(
            select(DailyStat).where(
                func.date(DailyStat.date) == today
            )
        )
        today_stat = result.scalar_one_or_none()

        # Всего за всё время
        total_users_result = await db.execute(select(func.count(User.id)))
        total_meetings_result = await db.execute(select(func.count(Meeting.id)))

        new_users = today_stat.new_users if today_stat else 0
        new_meetings = today_stat.new_meetings if today_stat else 0
        total_users = total_users_result.scalar()
        total_meetings = total_meetings_result.scalar()

    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"👤 Зарегистрировано сегодня: <b>{new_users}</b>\n"
        f"🗓️ Создано встреч сегодня: <b>{new_meetings}</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"📌 Всего встреч создано: <b>{total_meetings}</b>"
    )

    await update.message.reply_text(text, parse_mode="HTML")


# --- Обработчик для команды ---
stats_handler = CommandHandler('stats', send_stats)


# === ЕЖЕДНЕВНЫЙ ОТЧЁТ ===
async def schedule_daily_report(application: Application):
    """
    Настраивает ежедневную отправку отчёта в 20:00
    Вызывается один раз при запуске бота
    """
    async def send_daily_report(context: ContextTypes.DEFAULT_TYPE):
        job = context.job
        admin_id = job.data.get("admin_id")

        try:
            # Подготавливаем фейковый Update
            fake_update = Update(
                update_id=0,
                message=None  # заглушка, будет создана ниже
            )
            fake_update.message = type("Message", (), {
                "chat_id": admin_id,
                "reply_text": lambda text, parse_mode, **kwargs: context.bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode=parse_mode
                ),
                "from_user": type("User", (), {"id": admin_id})
            })()
            fake_update.effective_user = fake_update.message.from_user

            # Вызываем send_stats как будто /stats
            await send_stats(fake_update, context)
        except Exception as e:
            logger.error("❌ Ошибка при отправке ежедневного отчёта: %s", e)

    # Планируем задачу
    application.job_queue.run_daily(
        send_daily_report,
        time=time(hour=20, minute=0, second=0),  # 20:00 по времени сервера
        data={"admin_id": ADMIN_USER_ID},
        name="daily_stats_report"
    )
    logger.info("✅ Ежедневный отчёт запланирован на 20:00")
