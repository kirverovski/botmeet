"""
main.py — Оптимизированная точка входа бота
Совместима с: Windows, Linux, WSL, Render, Railway
"""

import logging
import sys
import asyncio
import platform
from typing import Dict, Any
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ExtBot,
)
from config import TELEGRAM_API_KEY, WEBHOOK_URL, PORT
from redis_client import init_redis, close_redis


# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# === Асинхронный event loop: uvloop (если доступен) ===
if platform.system() != "Windows":
    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        logger.info("🚀 uvloop активирован — ускорение асинхронности")
    except ImportError:
        logger.warning("⚠️ uvloop не установлен — используется стандартный asyncio")
else:
    # Для Windows
    if sys.version_info >= (3, 8):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            logger.info("🔄 Windows: установлен WindowsSelectorEventLoopPolicy")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось установить WindowsSelectorEventLoopPolicy: {e}")


# === Ленивые импорты — чтобы избежать циклов и ускорить запуск ===
def get_handlers():
    """Ленивый импорт всех обработчиков"""
    from registration import registration_conv
    from meetings import meeting_conv
    from meetings import show_chat_help, send_chat_instruction_video
    from all import (
        send_welcome,
        show_my_meetings,
        handle_main_menu_buttons,
        handle_my_own_meetings,
        handle_participate,
        handle_delete_meeting,
        confirm_delete_meeting,
        cancel_delete_meeting,
        handle_meeting_details,
        handle_leave_meeting,
        back_to_meeting,
        set_chat_link,
    )
    from searchmeetings import get_handlers as get_search_handlers
    from searchmeetings import (
        handle_show_more,
        handle_location,
        handle_category_selection,
        handle_find_meetings,
        handle_near_me,
        request_ai_search,
    )
    from ai_edit import get_handlers as get_ai_edit_handlers
    from participants import join_handler, leave_handler
    from edit_meeting import edit_conv

    handlers: Dict[str, Any] = {
        "registration_conv": registration_conv,
        "meeting_conv": meeting_conv,
        "send_welcome": send_welcome,
        "handle_main_menu_buttons": handle_main_menu_buttons,
        "handle_my_own_meetings": handle_my_own_meetings,
        "handle_participate": handle_participate,
        "handle_delete_meeting": handle_delete_meeting,
        "confirm_delete_meeting": confirm_delete_meeting,
        "cancel_delete_meeting": cancel_delete_meeting,
        "handle_meeting_details": handle_meeting_details,
        "back_to_meeting": back_to_meeting,
        "set_chat_link": set_chat_link,
        "join_handler": join_handler,
        "leave_handler": leave_handler,
        "send_chat_instruction_video": send_chat_instruction_video,
        "show_chat_help": show_chat_help,
        "handle_show_more": handle_show_more,
        "handle_location": handle_location,
        "handle_category_selection": handle_category_selection,
        "handle_find_meetings": handle_find_meetings,
        "handle_near_me": handle_near_me,
        "request_ai_search": request_ai_search,
        "edit_conv": edit_conv,
    }

    handlers.update(get_search_handlers())
    handlers.update(get_ai_edit_handlers())
    return handlers


# === Тестовая команда (опционально) ===
async def test_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Присоединиться", callback_data="join_999")],
        [InlineKeyboardButton("🔍 Подробнее", callback_data="details_999")]
    ])
    await update.message.reply_text("🧪 Тестовая встреча", reply_markup=markup)


# === Главная функция запуска бота ===
async def main():
    logger.info("🔧 Инициализация бота...")

    try:
        # === 🔌 Инициализация Redis ===
        logger.info("🔄 Подключение к Redis...")
        try:
            await init_redis()
            logger.info("✅ Redis успешно подключён")
        except Exception as e:
            logger.error("❌ Не удалось подключиться к Redis. Бот не будет запущен.")
            raise e

        # === 🛠 Инициализация БД ===
        logger.info("🔄 Инициализация базы данных...")
        from db import init_db
        await init_db()
        logger.info("✅ База данных инициализирована")

        # === 🏗️ Создание приложения ===
        logger.info("🏗️ Создание Telegram-приложения...")
        application = (
            Application.builder()
            .token(TELEGRAM_API_KEY)
            .post_init(post_init)  # Добавляем post_init
            .build()
        )

        # Хранение медиагрупп
        application.bot_data.setdefault("media_groups", {})

        # Регистрация обработчиков
        h = get_handlers()

        # === Группа 1: Conversation Handlers (высокий приоритет) ===
        application.add_handler(h["registration_conv"], group=1)
        application.add_handler(h["meeting_conv"], group=1)
        application.add_handler(h["edit_conv"], group=1)

        # === Группа 2: Геопозиция ===
        application.add_handler(
            MessageHandler(filters.LOCATION, h["handle_location"]),
            group=2
        )

        # === Группа 3: AI-редактирование ===
        async def ai_edit_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
            state = context.user_data.get("ai_edit_state")
            if state == "ai_what_to_edit":
                await h["handle_ai_edit_message"](update, context)
            elif state == "ai_waiting_save":
                await h["handle_ai_edit_finalize"](update, context)

        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, ai_edit_flow),
            group=3
        )

        # === Группа 4: AI-поиск (условный ввод) ===
        async def ai_search_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if context.user_data.get("awaiting_ai_query"):
                await h["handle_ai_query_input"](update, context)

        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, ai_search_flow),
            group=4
        )

        # === Группа 5: Основное меню (текст) ===
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                h["handle_main_menu_buttons"]
            ),
            group=5
        )

        # === Команды ===
        application.add_handler(CommandHandler("start", h["send_welcome"]))
        application.add_handler(CommandHandler("test", test_buttons))
        application.add_handler(CommandHandler("setchat", h["set_chat_link"]))

        async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
            from common import send_main_menu
            await send_main_menu(update.effective_chat.id, context)

        application.add_handler(CommandHandler("menu", cmd_menu))

        # === CallbackQuery Handlers (не в группах) ===
        application.add_handler(CallbackQueryHandler(h["handle_category_selection"], pattern=r"^cat_"))
        application.add_handler(CallbackQueryHandler(h["handle_find_meetings"], pattern="^find_meetings$"))
        application.add_handler(CallbackQueryHandler(h["handle_near_me"], pattern="^near_me$"))
        application.add_handler(CallbackQueryHandler(h["request_ai_search"], pattern="^ai_search$"))
        application.add_handler(CallbackQueryHandler(h["handle_my_own_meetings"], pattern="^my_own$"))
        application.add_handler(CallbackQueryHandler(h["handle_participate"], pattern="^participate$"))
        application.add_handler(CallbackQueryHandler(h["handle_delete_meeting"], pattern=r"^delete_\d+$"))
        application.add_handler(CallbackQueryHandler(h["confirm_delete_meeting"], pattern=r"^confirm_delete_\d+$"))
        application.add_handler(CallbackQueryHandler(h["cancel_delete_meeting"], pattern="^cancel_delete$"))
        application.add_handler(CallbackQueryHandler(h["handle_meeting_details"], pattern=r"^details_\d+$"))
        application.add_handler(CallbackQueryHandler(h["back_to_meeting"], pattern="^back_\\d+$"))
        application.add_handler(CallbackQueryHandler(h["handle_show_more"], pattern=r"^show_more_"))
        application.add_handler(CallbackQueryHandler(h["show_chat_help"], pattern="^show_chat_help$"))
        application.add_handler(CallbackQueryHandler(h["send_chat_instruction_video"], pattern="^send_chat_video$"))
        application.add_handler(h["join_handler"]) 
        application.add_handler(h["leave_handler"])

        
        # === Логирование (только в dev) ===
        if not WEBHOOK_URL:  # Только локально
            async def log_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
                logger.info(f"📥 UPDATE {update.update_id}: {update.message or update.callback_query.data if update.callback_query else 'no data'}")

            application.add_handler(MessageHandler(filters.ALL, log_update), group=99)

        # === Запуск бота ===
        async with application:
            await application.start()
            logger.info("✅ Бот запущен и подключён к Telegram")

            if WEBHOOK_URL:
                # Webhook (для Render, Railway и др.)
                port = int(PORT) if PORT else 8080
                logger.info(f"🌐 Активация webhook на порту {port}")
                await application.bot.set_webhook(url=WEBHOOK_URL)
                await application.updater.start_webhook(
                    listen="0.0.0.0",
                    port=port,
                    url_path=TELEGRAM_API_KEY,
                    webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_API_KEY}"
                )
            else:
                # Polling (локально)
                logger.info("🔄 Запуск через polling...")
                await application.updater.start_polling(
                    poll_interval=2.0,
                    timeout=20,
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True
                )

            logger.info("🚀 Бот готов к работе. Ожидание обновлений...")
            await asyncio.Event().wait()

    except Exception as e:
        logger.exception("❌ КРИТИЧЕСКАЯ ОШИБКА: %s", e)
        sys.exit(1)
    finally:
        # === 🔐 Гарантированное закрытие Redis ===
        logger.info("🛑 Завершение работы: закрытие Redis...")
        try:
            await close_redis()
            logger.info("✅ Redis закрыт")
        except Exception as e:
            logger.error("❌ Ошибка при закрытии Redis: %s", e)


# === post_init — вызывается после инициализации приложения ===
async def post_init(application: Application) -> None:
    """Логирование после старта бота"""
    try:
        me = await application.bot.get_me()
        logger.info(f"🤖 Бот запущен как @{me.username}")
    except Exception as e:
        logger.error("❌ Не удалось получить имя бота: %s", e)


# === Точка входа ===
if __name__ == "__main__":
    try:
        # Запуск с uvloop (если установлен и не Windows)
        if platform.system() != "Windows" and 'uvloop' in sys.modules:
            import uvloop
            logger.info("🌀 Запуск через uvloop...")
            uvloop.run(main())
        else:
            logger.info("🔄 Запуск через asyncio...")
            asyncio.run(main())
    except RuntimeError as e:
        if "Event loop is already running" in str(e):
            logger.warning("⚠️ Вложенный event loop — запускаем через create_task")
            asyncio.get_event_loop().create_task(main())
        else:
            raise
