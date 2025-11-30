"""
calendar_keyboard.py — Инлайн-календарь для Telegram (на русском, без "Сегодня" и "Завтра")
"""
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import calendar
from telegram.constants import ParseMode
import logging

logger = logging.getLogger(__name__)


# 📅 Названия месяцев на русском
MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
]


def create_calendar(year: int = None, month: int = None):
    """
    Создаёт инлайн-календарь:
    - Месяц на русском
    - Без кнопок "Сегодня" и "Завтра"
    """
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    # Заголовок: Месяц Год (на русском)
    month_name = MONTH_NAMES[month - 1]  # индекс с 0
    header = f"{month_name} {year}"

    # Кнопки дней недели (Пн Вт Ср Чт Пт Сб Вс)
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    day_buttons = [InlineKeyboardButton(day, callback_data="noop") for day in weekdays]
    rows = [day_buttons]

    # Генерация календарных недель
    cal = calendar.monthcalendar(year, month)
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="noop"))
            else:
                row.append(InlineKeyboardButton(str(day), callback_data=f"cal_day_{year}_{month}_{day}"))
        rows.append(row)

    # Кнопки навигации: ◀️ Месяц Год ▶️
    nav_row = [
        InlineKeyboardButton("◀️", callback_data=f"cal_prev_{year}_{month}"),
        InlineKeyboardButton(header, callback_data="noop"),
        InlineKeyboardButton("▶️", callback_data=f"cal_next_{year}_{month}"),
    ]
    rows.append(nav_row)

    # 🔴 УБРАНО: кнопки "Сегодня" и "Завтра" — больше не добавляются

    return InlineKeyboardMarkup(rows)

# --- Обработчики ---
async def handle_calendar_query(update, context):
    """
    Обрабатывает календарь и возвращает состояние.
    Отправку следующего шага берёт на себя meetings.py.
    """
    query = update.callback_query
    await query.answer()

    data = query.data

    # --- Навигация по месяцам ---
    if data.startswith("cal_prev_") or data.startswith("cal_next_"):
        direction = -1 if data.startswith("cal_prev_") else 1
        try:
            _, action, year_str, month_str = data.split("_", 3)
            year, month = int(year_str), int(month_str)

            month += direction
            if month < 1:
                month = 12
                year -= 1
            elif month > 12:
                month = 1
                year += 1

            context.user_data['calendar_year'] = year
            context.user_data['calendar_month'] = month

            markup = create_calendar(year, month)
            await query.edit_message_reply_markup(reply_markup=markup)
        except Exception as e:
            print(f"Ошибка навигации: {e}")
        return 5  # MEETING_DATE

    # --- Выбор даты ---
    if data.startswith("cal_day_"):
        try:
            _, _, year_str, month_str, day_str = data.split("_", 5)
            year, month, day = int(year_str), int(month_str), int(day_str)
            selected_date = datetime(year, month, day)

            if selected_date.date() < datetime.now().date():
                await query.answer("❌ Прошлая дата недоступна", show_alert=True)
                return 5

            context.user_data['date_time'] = selected_date

            # Удаляем календарь
            try:
                await context.bot.delete_message(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id
                )
            except Exception as e:
                print(f"Не удалось удалить календарь: {e}")

            # ✅ Возвращаем состояние → meetings.py сам отправит сообщение
            return 6  # MEETING_TIME

        except Exception as e:
            print(f"Ошибка выбора даты: {e}")
            await query.answer("❌ Ошибка выбора даты.")
            return 5
  # Остаемся в MEETING_DATE


