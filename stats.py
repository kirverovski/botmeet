"""
stats.py — логика сбора и отображения статистики
"""
from datetime import datetime
from sqlalchemy import select, func
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

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
