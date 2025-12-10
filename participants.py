"""
participants.py — обработка присоединения и выхода из встреч
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes
from sqlalchemy import select
from db import Meeting, MeetingParticipant, User, get_db
from logic import is_user_registered
from constant import JOIN_PREFIX, LEAVE_PREFIX
import logging

logger = logging.getLogger(__name__)


async def handle_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"📥 handle_join вызван! callback_data = {update.callback_query.data}")
    
    query = update.callback_query
    user_id = query.from_user.id

    try:
        await query.answer()

        # Проверка регистрации
        if not await is_user_registered(user_id):
            await query.edit_message_text("⚠️ Для участия необходимо сначала зарегистрироваться.")
            return

        try:
            meeting_id = int(query.data.split("_", 1)[1])
        except (IndexError, ValueError):
            await query.answer("❌ Некорректный ID встречи.")
            return

        async with get_db() as db:
            result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
            meeting = result.scalar_one_or_none()
            if not meeting:
                await query.answer("❌ Встреча не найдена.")
                return

            result = await db.execute(select(User).where(User.telegram_id == user_id))
            user = result.scalar_one_or_none()

            if not user:
                await query.answer("❌ Пользователь не найден.")
                return

            result = await db.execute(
                select(MeetingParticipant).where(
                    MeetingParticipant.meeting_id == meeting_id,
                    MeetingParticipant.user_id == user_id
                )
            )
            if result.scalar_one_or_none():
                await query.answer("Вы уже участвуете в этой встрече!")
                return

            if meeting.current_participants >= meeting.max_participants:
                await query.answer("🚫 Нет свободных мест.")
                return

            if meeting.min_age and user.age < meeting.min_age:
                await query.answer(f"❌ Минимальный возраст: {meeting.min_age} лет.")
                return
            if meeting.max_age and user.age > meeting.max_age:
                await query.answer(f"❌ Максимальный возраст: {meeting.max_age} лет.")
                return

            # Добавляем участника
            participation = MeetingParticipant(meeting_id=meeting_id, user_id=user_id)
            db.add(participation)
            meeting.current_participants += 1
            await db.commit()
            await db.refresh(meeting)

        # ✅ Уведомление создателю — после закрытия сессии
        try:
            async with get_db() as db_notify:
                result = await db_notify.execute(
                    select(User.full_name, User.username).where(User.telegram_id == user_id)
                )
                user_data = result.first()
                if not user_data:
                    raise ValueError("Не удалось получить данные пользователя")

                user_name = user_data.full_name
                username = f"@{user_data.username}" if user_data.username else "Пользователь"

            await context.bot.send_message(
                chat_id=meeting.creator_id,
                text=f"👤 <b>{user_name}</b> ({username}) присоединился(-лась) к вашей встрече:\n\n"
                     f"📌 <b>{meeting.title}</b>\n"
                     f"📅 {meeting.date_time.strftime('%d.%m %H:%M')}\n"
                     f"📍 {meeting.address}\n\n"
                     f"👥 Теперь участников: {meeting.current_participants}/{meeting.max_participants}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"⚠️ Не удалось отправить уведомление создателю {meeting.creator_id}: {e}")

        # Формируем текст для обновления сообщения
        location_text = meeting.address or f"{meeting.latitude:.6f}, {meeting.longitude:.6f}"
        new_text = (
            f"📌 <b>{meeting.title}</b>\n"
            f"📅 {meeting.date_time.strftime('%d.%m %H:%M')}\n"
            f"📍 {location_text}\n"
            f"👥 {meeting.current_participants}/{meeting.max_participants}"
        )
        if meeting.description:
            new_text += f"\n\n{meeting.description}"

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Покинуть", callback_data=f"{LEAVE_PREFIX}{meeting_id}")],
            [InlineKeyboardButton("🔍 Подробнее", callback_data=f"details_{meeting_id}")]
        ])

        # Попытка редактирования сообщения
        try:
            await query.edit_message_text(
                text=new_text,
                reply_markup=markup,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Не удалось отредактировать сообщение: {e}")
            # Если не получилось — отправляем новое
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=new_text,
                reply_markup=markup,
                parse_mode="HTML"
            )
            # И удаляем старое, если возможно
            try:
                await query.message.delete()
            except Exception as del_e:
                logger.warning(f"Не удалось удалить старое сообщение: {del_e}")

        await query.answer(f"✅ Вы присоединились к «{meeting.title}»!")

    except Exception as e:
        logger.exception("[JOIN] Ошибка при присоединении: %s", e)
        try:
            await query.answer("❌ Ошибка регистрации. Попробуйте позже.")
        except Exception:
            pass  # Игнорируем, если сообщение уже удалено


async def handle_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает выход пользователя из встречи.
    Отправляет уведомление создателю встречи.
    """
    query = update.callback_query
    user_id = query.from_user.id

    try:
        await query.answer()

        # Проверка регистрации
        if not await is_user_registered(user_id):
            await query.edit_message_text("⚠️ Вы не зарегистрированы.")
            return

        try:
            meeting_id = int(query.data.split("_", 1)[1])
        except (IndexError, ValueError):
            await query.answer("❌ Некорректный ID встречи.")
            return

        async with get_db() as db:
            result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
            meeting = result.scalar_one_or_none()

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
            participation = result.scalar_one_or_none()

            if not participation:
                await query.answer("Вы не участвуете в этой встрече.")
                return

            # Удаление участника
            await db.delete(participation)
            meeting.current_participants = max(0, meeting.current_participants - 1)
            await db.commit()
            await db.refresh(meeting)

        # ✅ Уведомление создателю о выходе участника
        try:
            async with get_db() as db_notify:
                result = await db_notify.execute(
                    select(User.full_name, User.username).where(User.telegram_id == user_id)
                )
                user_data = result.first()
                if not user_data:
                    raise ValueError("Не удалось получить данные пользователя")

                user_name = user_data.full_name
                username = f"@{user_data.username}" if user_data.username else "Пользователь"

            await context.bot.send_message(
                chat_id=meeting.creator_id,
                text=f"👤 <b>{user_name}</b> ({username}) покинул(-а) вашу встречу:\n\n"
                     f"📌 <b>{meeting.title}</b>\n"
                     f"📅 {meeting.date_time.strftime('%d.%m %H:%M')}\n"
                     f"📍 {meeting.address}\n\n"
                     f"👥 Осталось: {meeting.current_participants}/{meeting.max_participants}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"⚠️ Не удалось отправить уведомление о выходе создателю {meeting.creator_id}: {e}")

        # Обновление сообщения
        location_text = meeting.address or f"{meeting.latitude:.6f}, {meeting.longitude:.6f}"
        new_text = (
            f"📌 <b>{meeting.title}</b>\n"
            f"📅 {meeting.date_time.strftime('%d.%m %H:%M')}\n"
            f"📍 {location_text}\n"
            f"👥 {meeting.current_participants}/{meeting.max_participants}"
        )
        if meeting.description:
            new_text += f"\n\n{meeting.description}"

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Присоединиться", callback_data=f"{JOIN_PREFIX}{meeting_id}")],
            [InlineKeyboardButton("🔍 Подробнее", callback_data=f"details_{meeting_id}")]
        ])

        try:
            await query.message.edit_text(
                text=new_text,
                reply_markup=markup,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Не удалось отредактировать сообщение: {e}")
            # Если невозможно — удаляем и отправляем новое
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=new_text,
                reply_markup=markup,
                parse_mode="HTML"
            )

        await query.answer(f"✅ Вы покинули «{meeting.title}».")

    except Exception as e:
        logger.exception("[LEAVE] Ошибка при выходе пользователя %s из встречи %s: %s", user_id, meeting_id, e)
        try:
            await query.answer("❌ Ошибка. Попробуйте позже.")
        except Exception:
            pass  # Игнорируем, если сообщение уже удалено

join_handler = CallbackQueryHandler(handle_join, pattern=f"^{JOIN_PREFIX}")
leave_handler = CallbackQueryHandler(handle_leave, pattern=f"^{LEAVE_PREFIX}")
