import logging
import re
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlparse, parse_qs, unquote
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload
from telegram import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Update,
    InputMediaPhoto,
)
from telegram.constants import ParseMode
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ApplicationBuilder,
    filters
)
from constant import *
from common import (
    user_states, send_main_menu
)
from config import YANDEX_API_KEY
from participants import handle_join
from logic import (
        is_user_registered, get_main_keyboard,
)
from db import Meeting, User, MeetingParticipant, get_db
from datetime import datetime
import aiohttp
import json
import calendar

# Логгер
logger = logging.getLogger(__name__)

# --- 1. /start ---
async def send_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие и главное меню."""
    user_id = update.effective_user.id
    logger.info(f"[WELCOME] Пользователь {user_id} запустил бота")
    await send_main_menu(chat_id=update.effective_chat.id, context=context)

# --- 2. Обработчик нажатий на кнопки главного меню ---
async def handle_main_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых кнопок меню."""
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # Защита от вмешательства в диалоги
    if context.user_data.get('creating_meeting') or context.user_data.get('handling_registration'):
        logger.debug(f"[MENU] Пользователь {user_id} в процессе действия — игнорируем")
        return

    context.user_data['handled_menu_press'] = True
    registered = await is_user_registered(user_id)

    # Логика кнопок
    if "СОЗДАТЬ" in text.upper() and "ВСТРЕЧ" in text.upper():
        return  # Пусть сработает ConversationHandler

    elif text == "🔍 НАЙТИ ВСТРЕЧУ":
        from searchmeetings import handle_find_meetings
        await handle_find_meetings(update, context)
        return

    elif text == "👥 Мои встречи" and registered:
        await show_my_meetings(update, context)
        return

    elif text == "💡 Инфо":
        info_text = (
            "📘 <b>О боте</b>\n\n"
            "Этот бот помогает находить и создавать встречи по интересам — "
            "от кофе до настольных игр.\n\n"
            "📌 <b>Что можно делать:</b>\n"
            "• Создавать встречи — кнопка «➕ СОЗДАТЬ ВСТРЕЧУ»\n"
            "• Искать встречи по интересам — «🔍 НАЙТИ ВСТРЕЧУ»\n"
            "• Посмотреть свои встречи — «👥 Мои встречи»:\n"
            "   — созданные вами\n"
            "   — редактировать или удалить\n"
            "   — те, к которым вы присоединились\n\n"
            "Бот в ранней стадии разработки.\n\n"
            "🛠 <b>Если что-то зависло:</b>\n"
            "🔁 <b>Начните процесс заново:</b>\n"
            "1. Нажмите «➕ СОЗДАТЬ ВСТРЕЧУ»</b>\n"
            " или «🔍 НАЙТИ ВСТРЕЧУ»\n"
            "2. Или введите /start\n\n"
            "✨ Спасибо за понимание!"
        )
        await update.message.reply_text(
            info_text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return

       
# --- 3. Мои встречи ---
async def show_my_meetings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать выбор: свои встречи или участие"""
    user_id = update.effective_user.id
    if not await is_user_registered(user_id):
        await update.message.reply_text("⚠️ Пройдите регистрацию, чтобы видеть свои встречи.")
        return

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Созданные", callback_data="my_own")],
        [InlineKeyboardButton("👥 Участвую", callback_data="participate")]
    ])
    
    await update.message.reply_text(
        "👀 Выберите тип встреч:",
        reply_markup=markup
    )

async def get_meeting_owner_markup(meeting: Meeting) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 ИИ", callback_data=f"edit_ai_{meeting.id}"),
            InlineKeyboardButton("✍️ Ручное", callback_data=f"edit_manual_{meeting.id}")
        ],
        [
            InlineKeyboardButton("🔍 Детали", callback_data=f"details_{meeting.id}")
        ],
        [
            InlineKeyboardButton("👥 Участники", callback_data=f"view_participants_{meeting.id}")
        ],
        [
            InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{meeting.id}")
        ]
    ])
async def handle_view_participants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Показать список участников встречи (только для создателя).
    """
    query = update.callback_query
    try:
        meeting_id = int(query.data.split("_")[2])  # view_participants_<id>
    except (IndexError, ValueError):
        await query.answer("❌ Неверный ID встречи.")
        return

    user_id = query.from_user.id
    await query.answer()

    async with get_db() as db:
        # Получаем встречу
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            await query.edit_message_text("❌ Встреча не найдена.")
            return

        if meeting.creator_id != user_id:
            await query.answer("❌ Только создатель может просматривать участников.", show_alert=True)
            return

        # Получаем участников с именами и юзернеймами
        result = await db.execute(
            select(User.full_name, User.username)
            .join(MeetingParticipant, User.telegram_id == MeetingParticipant.user_id)
            .where(MeetingParticipant.meeting_id == meeting_id)
        )
        participants = result.all()

    if not participants:
        text = "🤷‍♂️ Пока никто не присоединился."
    else:
        text = "👥 <b>Участники встречи:</b>\n\n"
        for i, (full_name, username) in enumerate(participants, 1):
            if username:
                text += f"{i}. {full_name} (@{username})\n"
            else:
                text += f"{i}. {full_name} (без юзернейма)\n"

    text += f"\n✅ Всего: {len(participants)}"

    # Кнопка "Назад"
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"back_to_owner_{meeting_id}")]
    ])

    await query.edit_message_text(text=text, reply_markup=markup, parse_mode=ParseMode.HTML)

async def back_to_owner_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Возврат к меню создателя встречи.
    """
    query = update.callback_query
    await query.answer()

    try:
        meeting_id = int(query.data.split("_")[-1])  # back_to_owner_<id>
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Ошибка: неизвестная встреча.")
        return

    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            await query.edit_message_text("❌ Встреча не найдена.")
            return

    markup = await get_meeting_owner_markup(meeting)
    await query.edit_message_text(
        text=f"Управление встречей:\n\n📌 <b>{meeting.title}</b>",
        reply_markup=markup,
        parse_mode=ParseMode.HTML
    )


async def handle_my_own_meetings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия 'Созданные'"""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    async with get_db() as db:
        result = await db.execute(
            select(Meeting).where(Meeting.creator_id == user_id)
        )
        meetings = result.scalars().all()

    if not meetings:
        await query.edit_message_text("📋 У вас пока нет созданных встреч.")
        return

    for meeting in meetings:
        # Подсчёт участников
        result = await db.execute(
            select(func.count(MeetingParticipant.user_id)).where(
                MeetingParticipant.meeting_id == meeting.id
            )
        )
        current = result.scalar() or 1

        # Форматируем текст
        text = (
            f"📌 <b>{meeting.title}</b>\n"
            f"📅 {meeting.date_time.strftime('%d.%m %H:%M')}\n"
            f"📍 {meeting.address}\n"
            f"👥 {current}/{meeting.max_participants}"
        )

        # Отправка с фото или без
        if meeting.photos_data:
            try:
                photos = json.loads(meeting.photos_data)
                media = [InputMediaPhoto(media=p['file_id']) for p in photos[:10]]
                
                # Отправляем фото
                sent = await context.bot.send_media_group(
                    chat_id=query.message.chat_id,
                    media=media
                )
                # Отправляем текст отдельно
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    reply_markup=await get_meeting_owner_markup(meeting),
                    parse_mode="HTML"
                )
                continue
            except Exception as e:
                logger.error(f"Ошибка отправки фото: {e}")

        # Без фото
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text,
            reply_markup=await get_meeting_owner_markup(meeting),
            parse_mode="HTML"
        )

    await context.bot.delete_message(
        chat_id=query.message.chat_id,
        message_id=query.message.message_id
    )


# --- 5. Встречи, в которых пользователь участвует ---
async def handle_participate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия 'Участвую'"""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    async with get_db() as db:
        result = await db.execute(
            select(MeetingParticipant).join(Meeting).where(
                MeetingParticipant.user_id == user_id,
                Meeting.creator_id != user_id
            ).options(joinedload(MeetingParticipant.meeting))
        )
        participations = result.scalars().all()

    if not participations:
        await query.edit_message_text("📋 Вы не участвуете ни в одной встрече.")
        return

    for part in participations:
        meeting = part.meeting
        result = await db.execute(
            select(func.count(MeetingParticipant.user_id)).where(
                MeetingParticipant.meeting_id == meeting.id
            )
        )
        current = result.scalar() or 1

        free = meeting.max_participants - current
        emoji = "🟢" if free > 4 else "🟡" if free > 0 else "🔴"
        status = (
            f"{emoji} Свободно {free} мест" if free > 1 else
            f"{emoji} Свободно 1 место" if free == 1 else
            f"{emoji} Мест нет"
        )

        text = (
            f"📌 <b>{meeting.title}</b>\n"
            f"📅 {meeting.date_time.strftime('%d.%m %H:%M')}\n"
            f"📍 {meeting.address}\n"
            f"{status}"
        )

        buttons = [[
            InlineKeyboardButton("🔸 Подробнее", callback_data=f"details_{meeting.id}"),
            InlineKeyboardButton("🚪 Покинуть", callback_data=f"{LEAVE_PREFIX}{meeting.id}")
        ]]
        if meeting.chat_link:
            buttons[0].insert(0, InlineKeyboardButton("💬 Чат", url=meeting.chat_link))

        markup = InlineKeyboardMarkup(buttons)

        if meeting.photos_data:
            try:
                photos = json.loads(meeting.photos_data)
                media = [InputMediaPhoto(media=p['file_id']) for p in photos[:1]]
                await context.bot.send_media_group(
                    chat_id=query.message.chat_id,
                    media=media
                )
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    reply_markup=markup,
                    parse_mode="HTML"
                )
                continue
            except Exception as e:
                logger.error(f"Ошибка фото: {e}")

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text,
            reply_markup=markup,
            parse_mode="HTML"
        )

    await context.bot.delete_message(
        chat_id=query.message.chat_id,
        message_id=query.message.message_id
    )


# --- 6. Удаление встречи ---
async def handle_delete_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос подтверждения удаления"""
    query = update.callback_query
    meeting_id = int(query.data.split("_")[1])
    user_id = query.from_user.id
    await query.answer()

    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting or meeting.creator_id != user_id:
            await query.answer("❌ Ошибка: вы не автор встречи.")
            return

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да", callback_data=f"confirm_delete_{meeting_id}"),
         InlineKeyboardButton("❌ Нет", callback_data="cancel_delete")]
    ])

    await query.edit_message_text(
        f"⚠️ Удалить встречу <b>{meeting.title}</b>?",
        reply_markup=markup,
        parse_mode="HTML"
    )


async def confirm_delete_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение удаления"""
    query = update.callback_query
    meeting_id = int(query.data.split("_")[2])
    await query.answer()

    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        if meeting:
            await db.delete(meeting)
            await db.commit()

    await query.edit_message_text("🗑️ Встреча удалена.")
    # Через 2 сек удаляем сообщение
    context.job_queue.run_once(
        lambda c: c.bot.delete_message(
            chat_id=query.message.chat_id,
            message_id=query.message.message_id
        ),
        2
    )


async def cancel_delete_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена удаления"""
    query = update.callback_query
    await query.answer("Отменено.")
    await context.bot.delete_message(
        chat_id=query.message.chat_id,
        message_id=query.message.message_id
    )

# --- 7. Детали встречи ---
async def handle_meeting_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показ подробной информации о встрече с учётом участия"""
    query = update.callback_query
    meeting_id = int(query.data.split("_")[1])
    await query.answer()

    user_id = query.from_user.id

    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            await query.answer("❌ Встреча не найдена.")
            return

        # Проверка участия
        result = await db.execute(
            select(MeetingParticipant).where(
                MeetingParticipant.meeting_id == meeting_id,
                MeetingParticipant.user_id == user_id
            )
        )
        is_participant = result.scalar() is not None
        is_creator = meeting.creator_id == user_id

        # 🔍 Получаем создателя по telegram_id (не по User.id!)
        result = await db.execute(
            select(User).where(User.telegram_id == meeting.creator_id)
        )
        creator = result.scalar_one_or_none()
        creator_username = creator.username if creator and creator.username else "Аноним"
        creator_display = f"@{creator_username}" if creator and creator.username else "Аноним"

        # Формируем текст
        text = (
            f"📌 <b>{meeting.title}</b>\n"
            f"💬 {meeting.description or 'Без описания'}"
            f"📅 <b>{meeting.date_time.strftime('%d.%m %H:%M')}</b>\n"
            f"📍 <b>{meeting.address}</b>\n"
            f"👥 {meeting.current_participants}/{meeting.max_participants}\n"
            f"🏷️ {meeting.category}\n"
            f"🔏 {meeting.privacy}\n"
            f"👤 Создатель: {creator_display}\n"
        )

        # 🔐 Показываем чат только участникам и создателю
        if meeting.chat_link:
            if is_participant or is_creator:
                text += f"\n\n💬 <a href='{meeting.chat_link}'>Чат встречи</a>"
            else:
                text += "\n\nℹ️ Эта встреча имеет общий чат. Он станет доступен после присоединения."

        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Назад", callback_data=f"back_{meeting.id}")
        ]])

        try:
            await query.edit_message_text(
                text=text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=False
            )
        except Exception as e:
            logger.error(f"Ошибка при редактировании сообщения: {e}")
            await query.answer("Ошибка отображения.")



async def back_to_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Возврат к сообщению встречи (не удаление).
    Ожидается формат: back_<meeting_id>
    """
    query = update.callback_query
    await query.answer()

    try:
        meeting_id = int(query.data.split("_")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Ошибка: неизвестная встреча.")
        return

    user_id = query.from_user.id

    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            await query.edit_message_text("❌ Встреча не найдена.")
            return

        # Проверка участия
        result = await db.execute(
            select(MeetingParticipant).where(
                MeetingParticipant.meeting_id == meeting_id,
                MeetingParticipant.user_id == user_id
            )
        )
        is_participant = result.scalar() is not None
        is_creator = meeting.creator_id == user_id

        # Текст встречи
        free = meeting.max_participants - meeting.current_participants
        status_text = (
            f"🟢 Свободно {free} {['место', 'места', 'мест'][min(free, 3) - 1]} из {meeting.max_participants}"
            if free > 0 else "🔴 Нет свободных мест"
        )

        text = (
            f"📌 <b>{meeting.title}</b>\n"
            f"📅 {meeting.date_time.strftime('%d.%m %H:%M')}\n"
            f"📍 {meeting.address}\n"
            f"{status_text}"
        )
        if meeting.description:
            text += f"\n\n{meeting.description}"

        # Кнопки
        if is_creator:
            buttons = [
                [InlineKeyboardButton("✅ Это ваша встреча", callback_data="own_meeting")],
                [InlineKeyboardButton("🔍 Подробнее", callback_data=f"details_{meeting.id}")]
            ]
        else:
            buttons = [
                [
                    InlineKeyboardButton(
                        "✅ Покинуть" if is_participant else "✅ Присоединиться",
                        callback_data=f"{LEAVE_PREFIX if is_participant else JOIN_PREFIX}{meeting.id}"
                    ),
                    InlineKeyboardButton("🔍 Подробнее", callback_data=f"details_{meeting.id}")
                ]
            ]

        markup = InlineKeyboardMarkup(buttons)

        # Редактируем сообщение
        await query.edit_message_text(
            text=text,
            reply_markup=markup,
            parse_mode="HTML"
        )

async def set_chat_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установка ссылки на чат (команда)"""
    if not context.args or len(context.args) != 2:
        await update.message.reply_text("Используйте: /setchat <ID> <ссылка>")
        return

    try:
        meeting_id = int(context.args[0])
        link = context.args[1].strip()
        if not link.startswith("https://t.me/"):
            await update.message.reply_text("❌ Неверный формат ссылки.")
            return

        user_id = update.effective_user.id
        async with get_db() as db:
            meeting = await db.get(Meeting, meeting_id)
            if not meeting:
                await update.message.reply_text("❌ Встреча не найдена.")
                return
            if meeting.creator_id != user_id:
                await update.message.reply_text("❌ Вы не автор.")
                return

            meeting.chat_link = link
            await db.commit()

        await update.message.reply_text("✅ Ссылка на чат добавлена!")

    except Exception as e:
        logger.error(f"set_chat_link: {e}")
        await update.message.reply_text("❌ Ошибка.")


async def handle_leave_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Покинуть встречу"""
    query = update.callback_query
    meeting_id = int(query.data.split("_")[1])
    user_id = query.from_user.id
    await query.answer()

    async with get_db() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            await query.answer("❌ Не найдено.")
            return

        result = await db.execute(
            select(MeetingParticipant).where(
                MeetingParticipant.meeting_id == meeting_id,
                MeetingParticipant.user_id == user_id
            )
        )
        part = result.scalar()
        if not part:
            await query.answer("Вы не участвуете.")
            return

        await db.delete(part)
        meeting.current_participants = max(0, meeting.current_participants - 1)
        await db.commit()

    await context.bot.delete_message(
        chat_id=query.message.chat_id,
        message_id=query.message.message_id
    )
    await query.answer(f"Вы покинули встречу.")


def get_handlers():
    """Возвращает все обработчики"""
    return {
        'start': CommandHandler('start', send_welcome),
        'main_menu': MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_main_menu_buttons
        ),
        'my_meetings': CallbackQueryHandler(show_my_meetings, pattern='^my_own$|^participate$'),
        'handle_own': CallbackQueryHandler(handle_my_own_meetings, pattern='^my_own$'),
        'handle_participate': CallbackQueryHandler(handle_participate, pattern='^participate$'),
        'delete_meeting': CallbackQueryHandler(handle_delete_meeting, pattern='^delete_\\d+$'),
        'confirm_delete': CallbackQueryHandler(confirm_delete_meeting, pattern='^confirm_delete_\\d+$'),
        'cancel_delete': CallbackQueryHandler(cancel_delete_meeting, pattern='^cancel_delete$'),
        'details': CallbackQueryHandler(handle_meeting_details, pattern='^details_\\d+$'),
        'back': CallbackQueryHandler(back_to_meeting, pattern='^back_\\d+$'),
        'leave': CallbackQueryHandler(handle_leave_meeting, pattern=f'^{LEAVE_PREFIX}\\d+$'),
        'set_chat': CommandHandler('setchat', set_chat_link),
        'join': CallbackQueryHandler(handle_join, pattern=f'^{JOIN_PREFIX}\\d+$'),
        'view_participants': CallbackQueryHandler(handle_view_participants, pattern=r'^view_participants_\d+$'),
        'back_to_owner': CallbackQueryHandler(back_to_owner_menu, pattern=r'^back_to_owner_\d+$'),
    }
