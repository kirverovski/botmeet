"""
edit_meeting.py — редактирование встречи: через ИИ или вручную.
"""
import logging
from typing import Optional

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)

# === 🔽 ВАЖНЫЕ ИМПОРТЫ, КОТОРЫЕ БЫЛИ УТЕРЯНЫ 🔽 ===
from sqlalchemy import func, select  # ← Критически важны!
from db import Meeting, get_db, MeetingParticipant
from ai_edit import (
    improve_title,
    improve_description,
    suggest_category,
    suggest_age_range,
)

# === Получение разметки — вручную, без зависимости от all.py ===
async def get_meeting_owner_markup(meeting_id: int) -> InlineKeyboardMarkup:
    """
    Генерация кнопок для создателя встречи.
    Вынесено из all.py, чтобы убрать циклические импорты.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🤖 ИИ", callback_data=f"edit_ai_{meeting_id}"),
                InlineKeyboardButton("✍️ Вручную", callback_data=f"edit_manual_{meeting_id}"),
            ],
            [InlineKeyboardButton("🔍 Детали", callback_data=f"details_{meeting_id}")],
            [InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{meeting_id}")],
        ]
    )


from constant import MEETING_CATEGORIES
from config import YANDEX_GPT_ENABLED

logger = logging.getLogger(__name__)

# --- Состояния ---
EDIT_MEETING_ID, WAITING_INSTRUCTION = range(2)

# Для ручного редактирования
(
    MANUAL_EDIT_ID,
    EDIT_TITLE,
    EDIT_DESCRIPTION,
    EDIT_CATEGORY,
    EDIT_AGE_MIN,
    EDIT_AGE_MAX,
) = range(2, 8)  # Убрали EDIT_CONFIRM — он не используется


# === 🤖 ИИ-РЕДАКТИРОВАНИЕ ===
async def start_ai_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        meeting_id = int(query.data.split("_")[2])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Ошибка: неверный ID.")
        return ConversationHandler.END

    context.user_data['edit_meeting_id'] = meeting_id
    context.user_data['edit_origin'] = 'ai'

    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            await query.edit_message_text("❌ Встреча не найдена.")
            return ConversationHandler.END

    await query.edit_message_text(
        f"📝 <b>Редактирование через ИИ</b>\n"
        f"Текущее название: <i>{meeting.title}</i>\n\n"
        "Напишите, как улучшить:\n"
        "• Сделай веселее\n"
        "• Сократи название\n"
        "• Для молодёжи 18–30\n"
        "• Добавь про кофе",
        parse_mode="HTML"
    )
    return WAITING_INSTRUCTION


async def handle_ai_instruction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    meeting_id = context.user_data.get('edit_meeting_id')

    if not meeting_id:
        await update.message.reply_text("❌ Сессия устарела.")
        return ConversationHandler.END

    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            await update.message.reply_text("❌ Встреча не найдена.")
            return ConversationHandler.END

        updated_fields = {}

        # 📝 Название
        new_title = await improve_title(meeting.title, user_text)
        if new_title and new_title != meeting.title:
            updated_fields['title'] = new_title

        # 📄 Описание
        new_desc = await improve_description(
            title=meeting.title,
            category=meeting.category,
            current=meeting.description or "",
            instruction=user_text
        )
        if new_desc and new_desc != (meeting.description or ""):
            updated_fields['description'] = new_desc

        # 🏷️ Категория
        new_cat = await suggest_category(meeting.title, meeting.description or "")
        if new_cat and new_cat != meeting.category:
            updated_fields['category'] = new_cat

        # 👶 Возраст
        age_suggestion = await suggest_age_range(meeting.description or "")
        if age_suggestion:
            updated_fields.update(age_suggestion)

        # Сохраняем
        if updated_fields:
            for k, v in updated_fields.items():
                setattr(meeting, k, v)
            await db.commit()
            await db.refresh(meeting)

            changes = "\n".join([f"✅ <b>{k}</b>: {v}" for k, v in updated_fields.items()])
            text = f"🎉 Обновлено:\n\n{changes}"
        else:
            text = "ℹ️ Нет изменений. Уточните запрос."

        # Показываем результат
        result = await db.execute(
            select(func.count(MeetingParticipant.user_id)).where(
                MeetingParticipant.meeting_id == meeting.id
            )
        )
        current = result.scalar() or 1

        final_text = (
            f"📌 <b>{meeting.title}</b>\n"
            f"📅 {meeting.date_time.strftime('%d.%m %H:%M')}\n"
            f"📍 {meeting.address}\n"
            f"👥 {current}/{meeting.max_participants}"
        )
        if meeting.description:
            final_text += f"\n\n💬 {meeting.description}"

        await update.message.reply_text(
            final_text,
            reply_markup=await get_meeting_owner_markup(meeting_id),
            parse_mode="HTML"
        )

    return ConversationHandler.END


# === ✍️ РУЧНОЕ РЕДАКТИРОВАНИЕ ===
async def start_manual_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        meeting_id = int(query.data.split("_")[2])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Ошибка ID.")
        return ConversationHandler.END

    context.user_data['edit_meeting_id'] = meeting_id
    context.user_data['edit_origin'] = 'manual'

    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            await query.edit_message_text("❌ Не найдено.")
            return ConversationHandler.END

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Название", callback_data="edit_field_title")],
        [InlineKeyboardButton("📄 Описание", callback_data="edit_field_desc")],
        [InlineKeyboardButton("🏷️ Категория", callback_data="edit_field_cat")],
        [InlineKeyboardButton("👶 Возраст", callback_data="edit_field_age")],
        [InlineKeyboardButton("✅ Готово", callback_data="edit_save")],
        [InlineKeyboardButton("❌ Отмена", callback_data="edit_cancel")],
    ])

    await query.edit_message_text(
        f"🛠️ Редактирование: <b>{meeting.title}</b>\n"
        "Выберите, что хотите изменить:",
        reply_markup=markup,
        parse_mode="HTML"
    )
    return MANUAL_EDIT_ID


# --- Подменю редактирования ---
async def edit_field_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✏️ Введите новое название:")
    return EDIT_TITLE


async def edit_title_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text or len(text) > 100:
        await update.message.reply_text("❌ От 1 до 100 символов.")
        return EDIT_TITLE

    meeting_id = context.user_data['edit_meeting_id']
    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        meeting.title = text
        await db.commit()

    await update.message.reply_text(f"✅ Название изменено на: <i>{text}</i>", parse_mode="HTML")
    return await show_edit_menu(update, context)


async def edit_field_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📄 Введите новое описание:")
    return EDIT_DESCRIPTION


async def edit_desc_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) > 500:
        await update.message.reply_text("❌ Максимум 500 символов.")
        return EDIT_DESCRIPTION

    meeting_id = context.user_data['edit_meeting_id']
    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        meeting.description = text
        await db.commit()

    await update.message.reply_text("✅ Описание обновлено.")
    return await show_edit_menu(update, context)


async def edit_field_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    buttons = [[InlineKeyboardButton(cat, callback_data=f"cat_{cat}")] for cat in MEETING_CATEGORIES]
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_edit_menu")])
    markup = InlineKeyboardMarkup(buttons)

    await query.edit_message_text("🏷️ Выберите категорию:", reply_markup=markup)
    return EDIT_CATEGORY


async def edit_category_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        cat = query.data.split("_", 1)[1]
    except IndexError:
        await query.answer("❌ Ошибка.")
        return MANUAL_EDIT_ID

    if cat not in MEETING_CATEGORIES:
        await query.answer("❌ Нет такой категории.")
        return MANUAL_EDIT_ID

    meeting_id = context.user_data['edit_meeting_id']
    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        meeting.category = cat
        await db.commit()

    await query.answer(f"✅ Категория: {cat}")
    return await show_edit_menu(update, context)


async def edit_field_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔢 Введите минимальный возраст (0–120):")
    return EDIT_AGE_MIN


async def edit_age_min(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        age = int(update.message.text)
        if not 0 <= age <= 120:
            raise ValueError
        context.user_data['temp_min_age'] = age
        await update.message.reply_text("🔢 Введите максимальный возраст:")
        return EDIT_AGE_MAX
    except ValueError:
        await update.message.reply_text("❌ Число от 0 до 120.")
        return EDIT_AGE_MIN


async def edit_age_max(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        age = int(update.message.text)
        min_age = context.user_data.get('temp_min_age', 0)
        if not (0 <= age <= 120) or age < min_age:
            raise ValueError
        meeting_id = context.user_data['edit_meeting_id']
        async with get_db() as db:
            meeting = await db.get(Meeting, meeting_id)
            meeting.min_age = min_age
            meeting.max_age = age
            await db.commit()
        await update.message.reply_text(f"✅ Возраст: {min_age}–{age}")
    except ValueError:
        await update.message.reply_text("❌ Укажите корректный возраст.")
        return EDIT_AGE_MAX
    return await show_edit_menu(update, context)


# --- Меню и навигация ---
async def show_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    meeting_id = context.user_data['edit_meeting_id']
    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            await update.message.reply_text("❌ Встреча не найдена.")
            return ConversationHandler.END

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Название", callback_data="edit_field_title")],
        [InlineKeyboardButton("📄 Описание", callback_data="edit_field_desc")],
        [InlineKeyboardButton("🏷️ Категория", callback_data="edit_field_cat")],
        [InlineKeyboardButton("👶 Возраст", callback_data="edit_field_age")],
        [InlineKeyboardButton("✅ Готово", callback_data="edit_save")],
        [InlineKeyboardButton("❌ Отмена", callback_data="edit_cancel")],
    ])

    text = (f"🛠️ Редактирование: <b>{meeting.title}</b>\n"
            f"Категория: {meeting.category}\n"
            f"Возраст: {meeting.min_age or '—'}–{meeting.max_age or '—'}")

    if hasattr(update, 'message'):
        await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
        except:
            pass

    return MANUAL_EDIT_ID


# --- Сохранение и отмена ---
async def edit_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("✅ Сохранено")
    meeting_id = context.user_data['edit_meeting_id']

    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            await query.edit_message_text("❌ Не найдено.")
            return ConversationHandler.END

    result = await db.execute(
        select(func.count(MeetingParticipant.user_id)).where(
            MeetingParticipant.meeting_id == meeting.id
        )
    )
    current = result.scalar() or 1

    text = (f"📌 <b>{meeting.title}</b>\n"
            f"📅 {meeting.date_time.strftime('%d.%m %H:%M')}\n"
            f"📍 {meeting.address}\n"
            f"👥 {current}/{meeting.max_participants}")
    if meeting.description:
        text += f"\n\n💬 {meeting.description}"

    await query.edit_message_text(
        text,
        reply_markup=await get_meeting_owner_markup(meeting_id),
        parse_mode="HTML"
    )
    return ConversationHandler.END


async def edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("❌ Отменено")
    meeting_id = context.user_data['edit_meeting_id']

    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            await query.edit_message_text("❌ Не найдено.")
            return ConversationHandler.END

    result = await db.execute(
        select(func.count(MeetingParticipant.user_id)).where(
            MeetingParticipant.meeting_id == meeting.id
        )
    )
    current = result.scalar() or 1

    text = (f"📌 <b>{meeting.title}</b>\n"
            f"📅 {meeting.date_time.strftime('%d.%m %H:%M')}\n"
            f"📍 {meeting.address}\n"
            f"👥 {current}/{meeting.max_participants}")

    await query.edit_message_text(
        text,
        reply_markup=await get_meeting_owner_markup(meeting_id),
        parse_mode="HTML"
    )
    return ConversationHandler.END


# --- Обработчик кнопок ---
async def handle_edit_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "edit_save":
        return await edit_save(update, context)
    elif data == "edit_cancel":
        return await edit_cancel(update, context)
    elif data == "edit_field_title":
        return await edit_field_title(update, context)
    elif data == "edit_field_desc":
        return await edit_field_desc(update, context)
    elif data == "edit_field_cat":
        return await edit_field_cat(update, context)
    elif data == "edit_field_age":
        return await edit_field_age(update, context)
    elif data.startswith("cat_"):
        return await edit_category_choice(update, context)
    elif data == "back_to_edit_menu":
        return await show_edit_menu(update, context)


# === ConversationHandler ===
edit_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_ai_edit, pattern=r"^edit_ai_\d+$"),
        CallbackQueryHandler(start_manual_edit, pattern=r"^edit_manual_\d+$"),
    ],
    states={
        WAITING_INSTRUCTION: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_instruction)
        ],
        MANUAL_EDIT_ID: [
            CallbackQueryHandler(handle_edit_button, pattern=r"^edit_field_|^edit_save|^edit_cancel|^cat_|^back_to_edit_menu$")
        ],
        EDIT_TITLE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_title_input)
        ],
        EDIT_DESCRIPTION: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_desc_input)
        ],
        EDIT_CATEGORY: [
            CallbackQueryHandler(edit_category_choice, pattern=r"^cat_")
        ],
        EDIT_AGE_MIN: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_age_min)
        ],
        EDIT_AGE_MAX: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, edit_age_max)
        ],
    },
    fallbacks=[
        CommandHandler("cancel", lambda u, c: ConversationHandler.END),
        MessageHandler(filters.COMMAND, lambda u, c: ConversationHandler.END)
    ],
    per_user=True,
    allow_reentry=True,
    name="edit_meeting_conv",
)
