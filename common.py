#common.py

import logging
from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes
from db import get_db
from logic import is_user_registered, get_main_keyboard
import aiofiles 

logger = logging.getLogger(__name__)
# Хранилище состояний пользователей (временное)
user_states = {}

async def send_main_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, silent: bool = False, force: bool = False):
    try:
        is_registered = await is_user_registered(chat_id)
        markup = get_main_keyboard(is_registered)

        # Если не принудительно — проверяем, не спамим ли
        if not force:
            if context.user_data.get('last_menu_sent') == is_registered:
                return  # Не спамим

        # Отправляем главное меню
        await context.bot.send_message(
            chat_id=chat_id,
            text="Выберите действие:",
            reply_markup=markup,
            disable_notification=silent
        )
        context.user_data['last_menu_sent'] = is_registered
        logger.debug(f"✅ Меню отправлено пользователю {chat_id}, зарегистрирован: {is_registered}")

    except Exception as e:
        logger.warning(f"⚠️ Не удалось отправить главное меню {chat_id}: {e}")


