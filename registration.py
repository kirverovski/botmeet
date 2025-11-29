from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    CommandHandler,
    filters,
)
from db import User, get_db
from logic import is_user_registered
from common import send_main_menu
from sqlalchemy import select
import re
import logging

logger = logging.getLogger(__name__)

# Состояния регистрации
ASK_NAME, ASK_GENDER, ASK_AGE, ASK_CITY, ASK_PHOTO = range(5)


async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Начало регистрации — проверка, зарегистрирован ли пользователь.
    """
    user_id = update.effective_user.id
    logger.info("[REG] 🟢 /register вызван пользователем %s", user_id)

    try:
        if await is_user_registered(user_id):
            await update.effective_message.reply_text("Вы уже зарегистрированы!")
            return ConversationHandler.END

        context.user_data.clear()
        await update.effective_message.reply_text(
            "👤 Введите ваше имя:",
            reply_markup=ReplyKeyboardRemove()
        )
        return ASK_NAME

    except Exception as e:
        logger.exception("[REG] Ошибка при старте регистрации: %s", e)
        await update.effective_message.reply_text("❌ Произошла ошибка. Попробуйте позже.")
        return ConversationHandler.END


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 1: Получение имени.
    """
    user_id = update.effective_user.id
    name = update.message.text.strip()
    logger.info("[REG] 🟡 Пользователь %s ввёл имя: '%s'", user_id, name)

    if not name or len(name) < 2 or len(name) > 50:
        await update.effective_message.reply_text("❌ Имя должно быть от 2 до 50 символов. Попробуйте снова:")
        return ASK_NAME

    context.user_data['name'] = name

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👨 Мужской", callback_data="gender_male"),
            InlineKeyboardButton("👩 Женский", callback_data="gender_female"),
        ],
        [
            InlineKeyboardButton("⚧ Другой", callback_data="gender_other"),
        ]
    ])
    await update.effective_message.reply_text("Выберите ваш пол:", reply_markup=markup)
    return ASK_GENDER


async def handle_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 2: Выбор пола.
    """
    query = update.callback_query
    await query.answer()

    gender = query.data.split("_", 1)[1]
    context.user_data['gender'] = gender
    logger.info("[REG] ✅ Пол сохранён: %s", gender)

    await query.message.reply_text("🔢 Введите ваш возраст (1–120):")
    return ASK_AGE


async def ask_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 3: Возраст.
    """
    user_id = update.effective_user.id
    text = update.message.text.strip()

    try:
        age = int(text)
        if not (1 <= age <= 120):
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("❌ Введите корректный возраст (1–120):")
        return ASK_AGE

    context.user_data['age'] = age
    logger.info("[REG] ✅ Возраст сохранён: %d", age)

    await update.effective_message.reply_text("🏙️ Введите ваш город:")
    return ASK_CITY


async def ask_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 4: Город.
    """
    user_id = update.effective_user.id
    city = update.message.text.strip().lower()

    city = re.sub(r'\b(г|город|область|край|республика|район)\b', '', city, flags=re.IGNORECASE).strip()

    if not city or len(city) < 2 or len(city) > 100:
        await update.effective_message.reply_text("❌ Введите корректное название города (2–100 символов):")
        return ASK_CITY

    context.user_data['city'] = city
    logger.info("[REG] ✅ Город сохранён: %s", city)

    await update.effective_message.reply_text(
        "📸 Отправьте фотографию для аватарки.\n\n"
        "❗️ Поддерживается только фото (не файл)."
    )
    return ASK_PHOTO


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 5: Получение фото и сохранение в БД.
    """
    user_id = update.effective_user.id
    logger.info("[REG] 🖼️ Получено фото от пользователя %s", user_id)

    required = ['name', 'gender', 'age', 'city']
    if not all(context.user_data.get(k) for k in required):
        logger.warning("[REG] ❌ Недостаточно данных: %s", context.user_data)
        await update.effective_message.reply_text("❌ Ошибка. Начните регистрацию заново.")
        return ConversationHandler.END

    try:
        photo_file_id = update.message.photo[-1].file_id
        username = update.effective_user.username

        async with get_db() as db:
            # ✅ Исправлено: select() из sqlalchemy
            result = await db.execute(
                select(User).where(User.telegram_id == user_id)
            )
            user = result.scalar_one_or_none()

            if user is None:
                user = User(
                    telegram_id=user_id,
                    username=username,
                    full_name=context.user_data['name'],
                    gender=context.user_data['gender'],
                    age=context.user_data['age'],
                    photo_id=photo_file_id,
                )
                db.add(user)
                logger.info("[REG] ✅ Новый пользователь добавлен: %s", user_id)
            else:
                user.full_name = context.user_data['name']
                user.gender = context.user_data['gender']
                user.age = context.user_data['age']
                user.username = username
                user.photo_id = photo_file_id
                logger.info("[REG] ✅ Профиль обновлён: %s", user_id)

            await db.commit()
            await db.refresh(user)

        await update.effective_message.reply_text("🎉 Регистрация завершена! Добро пожаловать!")
        await send_main_menu(user_id, context)

    except Exception as e:
        logger.exception("[REG] ❌ Ошибка при сохранении пользователя %s: %s", user_id, e)
        await update.effective_message.reply_text(
            "❌ Произошла ошибка при регистрации. Попробуйте позже или начните заново."
        )
    finally:
        context.user_data.clear()

    return ConversationHandler.END


# === ConversationHandler ===
registration_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_registration, pattern="^start_registration$"),
        MessageHandler(
            filters.Regex(r"^(👤\s*)?ЗАРЕГИСТРИРОВАТЬСЯ$"),
            start_registration
        ),
    ],
    states={
        ASK_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)
        ],
        ASK_GENDER: [
            CallbackQueryHandler(handle_gender, pattern=r"^gender_(male|female|other)$")
        ],
        ASK_AGE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_age)
        ],
        ASK_CITY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ask_city)
        ],
        ASK_PHOTO: [
            MessageHandler(filters.PHOTO, handle_photo)
        ],
    },
    fallbacks=[
        CommandHandler("cancel", lambda u, c: ConversationHandler.END),
        MessageHandler(
            filters.COMMAND,
            lambda u, c: ConversationHandler.END
        ),
    ],
    per_user=True,
    allow_reentry=True,
    persistent=False,
    name="registration_conv",
    block=True,
)
