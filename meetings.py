from telegram import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    CommandHandler,
    filters,
)
from datetime import datetime, timedelta
from calendar_keyboard import create_calendar, handle_calendar_query
import re
import json
import logging
import aiohttp
import asyncio
from db import Meeting, MeetingParticipant, get_db
from logic import (
    extract_coordinates_from_yandex,
    is_user_registered,
    get_coords_from_yandex,
)
from constant import MEETING_CATEGORIES
from config import YANDEX_API_KEY

logger = logging.getLogger(__name__)

# --- Состояния ---
(
    MEETING_TITLE,
    MEETING_DESCRIPTION,
    MEETING_CATEGORY,
    MEETING_PRIVACY,
    MEETING_LOCATION,
    MEETING_DATE,
    MEETING_TIME,
    MEETING_PARTICIPANTS,
    AGE_RANGE_CHOICE,
    MIN_AGE_INPUT,
    MAX_AGE_INPUT,
    WANT_CHAT,
    WAITING_PHOTOS,
) = range(13)


def get_progress_text(step: int, total: int = 9) -> str:
    return f"📌 <b>Шаг {step}/{total}</b>\n"


async def create_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Начало создания встречи.
    """
    user_id = update.effective_user.id
    if not await is_user_registered(user_id):
        await update.effective_message.reply_text("⚠️ Пройдите регистрацию, чтобы создать встречу.")
        return ConversationHandler.END

    context.user_data.clear()
    logger.info("[MEETING] 🟢 Начало создания встречи пользователем %s", user_id)

    msg = await update.effective_message.reply_text(
        get_progress_text(1) + "Введите название встречи:",
        parse_mode=ParseMode.HTML,
    )
    context.user_data['message_id'] = msg.message_id
    return MEETING_TITLE


async def handle_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 1: Название.
    """
    text = update.message.text.strip()
    if not text:
        await update.effective_message.reply_text("❌ Название не может быть пустым.")
        return MEETING_TITLE
    if len(text) > 100:
        await update.effective_message.reply_text("❌ Максимум 100 символов.")
        return MEETING_TITLE

    context.user_data['title'] = text

    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=context.user_data['message_id']
        )
    except Exception as e:
        logger.debug(f"[MEETING] Не удалось удалить сообщение: {e}")

    msg = await update.effective_message.reply_text(
        get_progress_text(2) + "Введите описание встречи:",
        parse_mode=ParseMode.HTML,
    )
    context.user_data['message_id'] = msg.message_id
    return MEETING_DESCRIPTION


async def handle_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 2: Описание.
    """
    if not update.message or not update.message.text:
        return MEETING_DESCRIPTION

    text = update.message.text.strip()
    if re.match(r"^(?:\+|➕)?\s*СОЗДАТЬ\s+ВСТРЕЧУ$", text, re.IGNORECASE):
        return MEETING_DESCRIPTION
    if not text:
        await update.effective_message.reply_text("❌ Описание не может быть пустым.")
        return MEETING_DESCRIPTION

    context.user_data['description'] = text

    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=context.user_data['message_id']
        )
    except Exception as e:
        logger.debug(f"[MEETING] Не удалось удалить сообщение: {e}")

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Пропустить", callback_data="skip_photos")],
        [InlineKeyboardButton("📷 Добавить фото", callback_data="add_photos")],
    ])
    msg = await update.effective_message.reply_text(
        get_progress_text(3) + "Добавить фото к встрече?\n"
                               "Можно отправить до 5 фото (по одному или альбомом).",
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )
    context.user_data['message_id'] = msg.message_id
    return WAITING_PHOTOS


# --- Обработка фото ---
async def process_media_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка альбома фото.
    """
    media_group_id = update.message.media_group_id
    if 'media_group_ids' not in context.user_data:
        context.user_data['media_group_ids'] = {}
    if media_group_id not in context.user_data['media_group_ids']:
        context.user_data['media_group_ids'][media_group_id] = []

    context.user_data['media_group_ids'][media_group_id].append(update.message)

    # Ждём, пока Telegram отправит все фото
    await asyncio.sleep(1)

    messages = context.user_data['media_group_ids'][media_group_id]
    if len(messages) != len(set(m.message_id for m in messages)):
        return WAITING_PHOTOS  # Ещё не все

    photos = context.user_data.get('photos', [])
    for msg in messages:
        if len(photos) >= 5:
            break
        if msg.photo:
            file_id = msg.photo[-1].file_id
            photos.append({'file_id': file_id, 'caption': None})

    context.user_data['photos'] = photos
    await update.effective_message.reply_text(
        f"✅ Альбом добавлен ({len(photos)}/5).",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Готово", callback_data="photos_done")]
        ])
    )
    return WAITING_PHOTOS


async def ask_photos_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Запрос на завершение загрузки фото.
    """
    await update.effective_message.reply_text(
        "✅ Все фото загружены. Продолжить создание встречи?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Готово", callback_data="photos_done")]
        ])
    )
    return WAITING_PHOTOS


async def handle_waiting_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    query = update.callback_query
    if query:
        if query.data == "skip_photos":
            await query.answer()
            context.user_data['photos'] = []
            await send_category_keyboard(update, context)
            return MEETING_CATEGORY
        elif query.data == "add_photos":
            await query.answer()
            await query.edit_message_text(
                "📸 Отправьте фото (по одному или альбомом, до 5 шт).\n"
                "После отправки нажмите кнопку ниже:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Готово", callback_data="photos_done")]
                ])
            )
            context.user_data['photos'] = []
            context.user_data['media_group_ids'] = {}
            return WAITING_PHOTOS
        elif query.data == "photos_done":
            await query.answer()
            try:
                await context.bot.delete_message(
                    chat_id=query.message.chat.id,
                    message_id=context.user_data['message_id']
                )
            except Exception as e:
                logger.debug(f"[MEETING] Не удалось удалить сообщение: {e}")
            context.user_data['photos'] = context.user_data.get('photos', [])
            await send_category_keyboard(update, context)
            return MEETING_CATEGORY

    if update.message:
        if update.message.photo:
            if len(context.user_data.get('photos', [])) >= 5:
                await update.effective_message.reply_text("❌ Нельзя добавить больше 5 фото.")
                return WAITING_PHOTOS
            file_id = update.message.photo[-1].file_id
            context.user_data.setdefault('photos', []).append({'file_id': file_id, 'caption': None})
            await update.effective_message.reply_text(
                f"✅ Фото добавлено ({len(context.user_data['photos'])}/5)."
            )
            return WAITING_PHOTOS
        elif update.message.media_group_id:
            return await process_media_group(update, context)

    return WAITING_PHOTOS

async def finalize_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query and query.data == "photos_done":
        await query.answer()

    # Удаляем сообщение с кнопкой
    try:
        await context.bot.delete_message(
            chat_id=query.message.chat.id,
            message_id=context.user_data['message_id']
        )
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение: {e}")

    # Сохраняем фото
    context.user_data['photos'] = context.user_data.get('photos', [])
    await send_category_keyboard(update, context)
    return MEETING_CATEGORY

# --- Категория ---
async def send_category_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Отправляет клавиатуру с выбором категории.
    """
    buttons = [
        [InlineKeyboardButton(cat, callback_data=f"category_{cat}")]
        for cat in MEETING_CATEGORIES
    ]
    markup = InlineKeyboardMarkup(buttons)

    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{get_progress_text(3)}Выберите категорию встречи:",
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )
    context.user_data['message_id'] = msg.message_id
    return MEETING_CATEGORY


async def handle_category_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 3: Категория.
    """
    query = update.callback_query
    await query.answer()

    try:
        category = query.data.split("category_", 1)[1]
    except IndexError:
        await query.answer("❌ Ошибка выбора категории.")
        return MEETING_CATEGORY

    if category not in MEETING_CATEGORIES:
        await query.answer("❌ Неверная категория.")
        return MEETING_CATEGORY

    context.user_data['category'] = category

    try:
        await context.bot.delete_message(
            chat_id=query.message.chat.id,
            message_id=context.user_data['message_id']
        )
    except Exception as e:
        logger.debug(f"[MEETING] Не удалось удалить сообщение: {e}")

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔓 Открытая", callback_data="privacy_open"),
         InlineKeyboardButton("🔒 Закрытая", callback_data="privacy_closed")]
    ])

    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{get_progress_text(4)}Выберите тип встречи:",
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )
    context.user_data['message_id'] = msg.message_id
    return MEETING_PRIVACY


async def handle_privacy_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 4: Приватность.
    """
    query = update.callback_query
    await query.answer()

    try:
        privacy = query.data.split("privacy_", 1)[1]
    except IndexError:
        await query.answer("❌ Ошибка выбора типа.")
        return MEETING_PRIVACY

    if privacy not in ("open", "closed"):
        await query.answer("❌ Недопустимый тип.")
        return MEETING_PRIVACY

    context.user_data['privacy'] = privacy

    try:
        await context.bot.delete_message(
            chat_id=query.message.chat.id,
            message_id=context.user_data['message_id']
        )
    except Exception as e:
        logger.debug(f"[MEETING] Не удалось удалить сообщение: {e}")

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗺️ Выбрать на карте", url="https://yandex.ru/maps")]
    ])

    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{get_progress_text(5)}📍 Отправьте ссылку на место встречи (Яндекс.Карты):",
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )
    context.user_data['message_id'] = msg.message_id
    return MEETING_LOCATION



import urllib.parse

async def handle_map_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 5: Обработка места встречи.

    Поддерживает:
    - Полные ссылки Яндекс.Карт
    - Сокращённые ссылки (/maps/-/...) → переход к ручному вводу
    - Ручной ввод адреса: "Город, улица, дом"
    - Координаты: "55.7558, 37.6176" или "55.7558 37.6176"

    Формат адреса: [Город], [Улица], [Номер дома]
    Примеры:
        • Ростов-на-Дону, Луначарского, 237
        • Станица Егорлыкская, Центральная, 15
        • Москва, Тверская, 7

    Координаты: широта (lat), долгота (lon)
        • 47.218975, 39.722172
    """
    text = update.message.text.strip()

    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=context.user_data['message_id']
        )
    except Exception as e:
        logger.debug(f"[MEETING] Не удалось удалить сообщение: {e}")

    lat, lon = None, None
    source = None

    # 1. Проверяем, не сокращённая ли ссылка
    url_match = re.search(r'https?://[^\s]+', text)
    if url_match:
        url = url_match.group(0)
        if re.search(r'maps/\-/([A-Za-z0-9]+)', url):
            logger.info(f"🚫 Сокращённая ссылка: {url} — требуем ручной ввод")
            msg = await update.effective_message.reply_text(
                f"{get_progress_text(5)}⚠️ <b>Сокращённые ссылки не поддерживаются.</b>\n\n"
                "Пожалуйста, введите <b>адрес вручную</b> в одном из форматов:\n"
                "• <i>Город, улица, дом</i>\n"
                "• <i>Станица, улица, дом</i>\n\n"
                "📌 <b>Примеры:</b>\n"
                "• <code>Ростов-на-Дону, Луначарского, 237</code>\n"
                "• <code>Станица Егорлыкская, Центральная, 15</code>\n\n"
                "📬 Или отправьте координаты:\n"
                "• <code>47.218975, 39.722172</code>",
                parse_mode=ParseMode.HTML,
            )
            context.user_data['message_id'] = msg.message_id
            return MEETING_LOCATION

    # 2. Если есть URL — пытаемся извлечь координаты из него
    if url_match:
        coords = extract_coordinates_from_yandex(url)
        if coords:
            lat, lon = coords
            source = "url"
            logger.info(f"📍 Координаты извлечены из ссылки: {lat}, {lon}")

    # 3. Если не получилось — проверяем, не координаты ли это
    if not lat or not lon:
        # Поддержка: 47.218975, 39.722172  или  47.218975 39.722172
        coord_match = re.search(r'(-?\d+\.\d+)\s*[, ]\s*(-?\d+\.\d+)', text)
        if coord_match:
            a, b = map(float, coord_match.groups())
            # Проверяем диапазоны: lat ≈ 40–80, lon ≈ 20–150
            if (40 <= a <= 80 and 20 <= b <= 150):
                lat, lon = a, b
                source = "coords"
                logger.info(f"📍 Координаты из текста: {lat}, {lon}")
            elif (20 <= a <= 150 and 40 <= b <= 80):
                lat, lon = b, a
                source = "coords"
                logger.info(f"📍 Координаты (переставлены): {lat}, {lon}")

    # 4. Если до сих пор нет координат — считаем весь текст адресом (или чистим от URL)
    if not lat or not lon:
        address = text
        if url_match:
            # Убираем URL из текста
            address = re.sub(r'https?://[^\s]+', '', address).strip()
        address = address.strip()

        if len(address) < 3:
            msg = await update.effective_message.reply_text(
                f"{get_progress_text(5)}❌ Адрес слишком короткий.\n\n"
                "Введите адрес в формате:\n"
                "• <code>Город, улица, дом</code>\n\n"
                "Пример:\n"
                "• <code>Ростов-на-Дону, Соколова, 9</code>",
                parse_mode=ParseMode.HTML,
            )
            context.user_data['message_id'] = msg.message_id
            return MEETING_LOCATION

        try:
            logger.info(f"📍 Геокодирование адреса: {address}")
            geocode_url = (
                f"https://geocode-maps.yandex.ru/1.x/"
                f"?apikey={YANDEX_API_KEY}&format=json&geocode={urllib.parse.quote(address)}"
            )
            async with aiohttp.ClientSession() as session:
                async with session.get(geocode_url, timeout=8) as resp:
                    data = await resp.json()

            collection = data["response"]["GeoObjectCollection"]
            if not collection["featureMember"]:
                raise ValueError("Нет объектов в ответе геокодера")

            feature = collection["featureMember"][0]["GeoObject"]
            pos = feature["Point"]["pos"]
            lon_raw, lat_raw = map(float, pos.split())  # формат: "lon lat"
            lat, lon = lat_raw, lon_raw
            source = "address"
            logger.info(f"📍 Адрес геокодирован: {lat}, {lon}")

        except Exception as e:
            logger.warning(f"[MEETING] Геокодирование не удалось: {e}")
            msg = await update.effective_message.reply_text(
                f"{get_progress_text(5)}❌ Не удалось найти место.\n\n"
                "Проверьте формат:\n"
                "• <i>Город, улица, дом</i>\n\n"
                "Пример:\n"
                "• <code>Ростов-на-Дону, Луначарского, 237</code>\n\n"
                "📬 Или отправьте координаты:\n"
                "• <code>47.218975, 39.722172</code>",
                parse_mode=ParseMode.HTML,
            )
            context.user_data['message_id'] = msg.message_id
            return MEETING_LOCATION

    # 5. Обратное геокодирование для получения полного адреса
    try:
        reverse_url = (
            f"https://geocode-maps.yandex.ru/1.x/"
            f"?apikey={YANDEX_API_KEY}&format=json&geocode={lon},{lat}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(reverse_url, timeout=5) as resp:
                data = await resp.json()

        feature = data["response"]["GeoObjectCollection"]["featureMember"][0]["GeoObject"]
        full_address = feature["metaDataProperty"]["GeocoderMetaData"]["text"]

        # Извлечение города и региона
        address_parts = full_address.split(", ")
        city = "unknown"
        region = "unknown"

        for part in address_parts:
            p = part.strip().lower()
            if any(kw in p for kw in ["г ", "город", "станица", "посёлок", "село", "деревня"]):
                city = part.strip()
            elif any(kw in p for kw in ["область", "край", "республика"]):
                region = part.strip()

        if city == "unknown" and len(address_parts) > 1:
            city = address_parts[1]
        if region == "unknown" and len(address_parts) > 2:
            region = address_parts[2]

        city_display = city if city != "unknown" else region if region != "unknown" else "Ближайший населённый пункт"

    except Exception as e:
        logger.warning(f"[MEETING] Обратное геокодирование не удалось: {e}")
        full_address = f"{lat:.6f}, {lon:.6f}"
        city_display = "unknown"

    # === ✅ Сохраняем данные ===
    context.user_data.update({
        'latitude': lat,
        'longitude': lon,
        'address': full_address,
        'city': city_display,
    })

    # Переход к выбору даты
    now = datetime.now()
    context.user_data['calendar_year'] = now.year
    context.user_data['calendar_month'] = now.month
    markup = create_calendar(now.year, now.month)
    msg = await update.effective_message.reply_text(
        f"{get_progress_text(6)}✅ Место: <b>{full_address}</b>\n\n"
        "📅 Выберите дата встречи:",
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )
    context.user_data['message_id'] = msg.message_id
    return MEETING_DATE
# --- Дата (Новый календарь) ---
# --- Дата (Новый календарь) ---
async def handle_date_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 6: Выбор даты → после выбора — отправляем выбор времени.
    """
    query = update.callback_query
    if query and query.data.startswith("cal_"):
        # ← handle_calendar_query обработает, но не будет отправлять
        await handle_calendar_query(update, context)

        # Если дата выбрана — отправляем время
        if 'date_time' in context.user_data:
            selected_date = context.user_data['date_time']
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=context.user_data['message_id']
                )
            except Exception as e:
                logger.debug(f"[MEETING] Не удалено: {e}")

            # Отправляем кнопки времени
            time_markup = get_time_buttons_for_date(selected_date)
            msg = await update.effective_message.reply_text(
                f"{get_progress_text(7)}⏰ Выберите время встречи:",
                reply_markup=time_markup,
                parse_mode=ParseMode.HTML,
            )
            context.user_data['message_id'] = msg.message_id
            return MEETING_TIME

    return MEETING_DATE


# --- Время ---
def get_time_buttons_for_date(selected_date: datetime) -> InlineKeyboardMarkup:
    """
    Генерация кнопок времени.
    """
    now = datetime.now()
    buttons = []
    row = []

    start_hour = 8
    if selected_date.date() == now.date():
        start_hour = max(8, now.hour + 1)

    for hour in range(start_hour, 22):
        btn = InlineKeyboardButton(f"{hour:02d}:00", callback_data=f"time_{hour:02d}:00")
        row.append(btn)
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("✏️ Вручную", callback_data="manual_time")])
    return InlineKeyboardMarkup(buttons)


async def handle_time_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 7: Выбор времени.
    """
    query = update.callback_query
    if query and query.data == "manual_time":
        await query.answer()
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat.id,
                message_id=context.user_data['message_id']
            )
        except Exception as e:
            logger.debug(f"[MEETING] Не удалено: {e}")

        msg = await context.bot.send_message(
            chat_id=query.message.chat.id,
            text="✏️ Введите время в формате ЧЧ:ММ (например, 14:25):"
        )
        context.user_data['message_id'] = msg.message_id
        return MEETING_TIME

    if query and query.data.startswith("time_"):
        await query.answer()
        try:
            _, time_str = query.data.split("time_", 1)
            hour, minute = map(int, time_str.split(":"))
        except Exception:
            await query.answer("❌ Ошибка формата.")
            return MEETING_TIME

        if not (0 <= hour <= 23) or not (0 <= minute <= 59):
            await query.answer("❌ Некорректное время.")
            return MEETING_TIME

        selected_date = context.user_data.get('date_time')
        if not isinstance(selected_date, datetime):
            await query.answer("❌ Сначала выберите дату.")
            return MEETING_DATE

        now = datetime.now()
        if selected_date.date() == now.date():
            if hour < now.hour or (hour == now.hour and minute <= now.minute):
                await query.answer("❌ Прошлое время недоступно.", show_alert=True)
                return MEETING_TIME

        selected_date = selected_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        context.user_data['date_time'] = selected_date

        try:
            await context.bot.delete_message(
                chat_id=query.message.chat.id,
                message_id=context.user_data['message_id']
            )
        except Exception as e:
            logger.debug(f"[MEETING] Не удалено: {e}")

        msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"{get_progress_text(8)}👥 Введите макс. число участников (2–1000):",
            parse_mode=ParseMode.HTML,
        )
        context.user_data['message_id'] = msg.message_id
        return MEETING_PARTICIPANTS

    # Ввод вручную
    if update.message:
        text = update.message.text.strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", text):
            try:
                hour, minute = map(int, text.split(":"))
                if not (0 <= hour <= 23) or not (0 <= minute <= 59):
                    raise ValueError
            except ValueError:
                await update.effective_message.reply_text("❌ Введите ЧЧ:ММ (например, 14:25).")
                return MEETING_TIME

            selected_date = context.user_data.get('date_time')
            if not isinstance(selected_date, datetime):
                await update.effective_message.reply_text("❌ Сначала выберите дату.")
                return MEETING_DATE

            now = datetime.now()
            if selected_date.date() == now.date():
                if hour < now.hour or (hour == now.hour and minute <= now.minute):
                    await update.effective_message.reply_text("❌ Прошлое время недоступно.")
                    return MEETING_TIME

            selected_date = selected_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
            context.user_data['date_time'] = selected_date

            await update.effective_message.reply_text(f"✅ Время установлено: {hour:02d}:{minute:02d}")

            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"{get_progress_text(8)}👥 Введите макс. число участников (2–1000):",
                parse_mode=ParseMode.HTML,
            )
            context.user_data['message_id'] = msg.message_id
            return MEETING_PARTICIPANTS

        else:
            await update.effective_message.reply_text("Введите время в формате ЧЧ:ММ.")
            return MEETING_TIME

    return MEETING_TIME
# --- Участники и возраст ---
async def handle_max_participants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 8: Максимальное количество участников.
    """
    try:
        max_participants = int(update.message.text)
        if max_participants < 2 or max_participants > 1000:
            await update.effective_message.reply_text("❌ Укажите число от 2 до 1000.")
            return MEETING_PARTICIPANTS

        context.user_data['max_participants'] = max_participants

        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['message_id']
            )
        except Exception as e:
            logger.debug(f"[MEETING] Не удалось удалить сообщение: {e}")

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Указать диапазон", callback_data="set_age_range")],
            [InlineKeyboardButton("❌ Без ограничений", callback_data="no_age_limit")]
        ])

        msg = await update.effective_message.reply_text(
            f"{get_progress_text(8)}👶 Укажите возрастной диапазон:",
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
        context.user_data['message_id'] = msg.message_id
        return AGE_RANGE_CHOICE

    except ValueError:
        await update.effective_message.reply_text("❌ Введите число от 2 до 1000.")
        return MEETING_PARTICIPANTS


async def handle_age_range_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Шаг 9: Выбор возрастных ограничений.
    """
    query = update.callback_query
    await query.answer()

    if query.data == "no_age_limit":
        context.user_data['min_age'] = None
        context.user_data['max_age'] = None

        try:
            await context.bot.delete_message(
                chat_id=query.message.chat.id,
                message_id=context.user_data['message_id']
            )
        except Exception as e:
            logger.debug(f"[MEETING] Не удалось удалить сообщение: {e}")

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, добавить", callback_data="chat_yes")],
            [InlineKeyboardButton("❌ Нет, не нужно", callback_data="chat_no")]
        ])

        msg = await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=f"{get_progress_text(9)}💬 Добавить чат для участников?",
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
        context.user_data['message_id'] = msg.message_id
        return WANT_CHAT

    elif query.data == "set_age_range":
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat.id,
                message_id=context.user_data['message_id']
            )
        except Exception as e:
            logger.debug(f"[MEETING] Не удалось удалить сообщение: {e}")

        msg = await context.bot.send_message(
            chat_id=query.message.chat.id,
            text="🔢 Введите минимальный возраст (0–120):"
        )
        context.user_data['message_id'] = msg.message_id
        return MIN_AGE_INPUT

    return AGE_RANGE_CHOICE


async def handle_min_age_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Ввод минимального возраста.
    """
    try:
        min_age = int(update.message.text)
        if not (0 <= min_age <= 120):
            await update.effective_message.reply_text("❌ От 0 до 120 лет.")
            return MIN_AGE_INPUT

        context.user_data['min_age'] = min_age

        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['message_id']
            )
        except Exception as e:
            logger.debug(f"[MEETING] Не удалось удалить сообщение: {e}")

        msg = await update.effective_message.reply_text("🔢 Введите максимальный возраст (0–120):")
        context.user_data['message_id'] = msg.message_id
        return MAX_AGE_INPUT

    except ValueError:
        await update.effective_message.reply_text("❌ Введите число.")
        return MIN_AGE_INPUT

async def handle_max_age_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Ввод максимального возраста.
    После ввода — переход к выбору чата.
    """
    try:
        max_age = int(update.message.text)
        if not (0 <= max_age <= 120):
            await update.effective_message.reply_text("❌ От 0 до 120 лет.")
            return MAX_AGE_INPUT

        min_age = context.user_data.get('min_age')
        if min_age is not None and max_age < min_age:
            await update.effective_message.reply_text(
                f"❌ Макс. возраст не может быть меньше мин. ({min_age})."
            )
            return MAX_AGE_INPUT

        context.user_data['max_age'] = max_age

        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['message_id']
            )
        except Exception as e:
            logger.debug(f"[MEETING] Не удалось удалить сообщение: {e}")

        # Показываем кнопки: нужен ли чат?
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, добавить", callback_data="chat_yes")],
            [InlineKeyboardButton("❌ Нет, не нужно", callback_data="chat_no")]
        ])

        msg = await update.effective_message.reply_text(
            f"{get_progress_text(9)}💬 Добавить чат для участников?",
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
        context.user_data['message_id'] = msg.message_id
        return WANT_CHAT  # ✅ Переход к следующему шагу

    except ValueError:
        await update.effective_message.reply_text("❌ Введите число.")
        return MAX_AGE_INPUT

# --- Создание встречи ---
async def create_meeting_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Финализация: сохранение встречи в БД.
    """
    user_id = update.effective_user.id

    required = ['title', 'description', 'category', 'privacy',
                'latitude', 'longitude', 'address', 'date_time', 'max_participants']
    if not all(context.user_data.get(k) for k in required):
        await update.effective_message.reply_text("❌ Ошибка: недостаточно данных.")
        context.user_data.clear()
        return

    try:
        async with get_db() as db:
            meeting = Meeting(
                title=context.user_data['title'],
                description=context.user_data['description'],
                category=context.user_data['category'],
                privacy=context.user_data['privacy'],
                latitude=context.user_data['latitude'],
                longitude=context.user_data['longitude'],
                address=context.user_data['address'],
                date_time=context.user_data['date_time'],
                max_participants=context.user_data['max_participants'],
                min_age=context.user_data.get('min_age'),
                max_age=context.user_data.get('max_age'),
                chat_link=context.user_data.get('chat_link'),
                photos_data=json.dumps(context.user_data['photos']) if context.user_data.get('photos') else None,
                current_participants=1,
                creator_id=user_id,
                is_approved=False,
            )
            db.add(meeting)
            await db.commit()
            await db.refresh(meeting)

            # Сами себе участник
            db.add(MeetingParticipant(user_id=user_id, meeting_id=meeting.id))
            await db.commit()

        # Финальное сообщение
        age_text = ""
        if meeting.min_age is not None and meeting.max_age is not None:
            age_text = f"\n👶 Возраст: {meeting.min_age}–{meeting.max_age} лет"
        elif meeting.min_age is not None:
            age_text = f"\n👶 Мин. возраст: {meeting.min_age} лет"
        elif meeting.max_age is not None:
            age_text = f"\n👶 Макс. возраст: {meeting.max_age} лет"

        text = (
            f"✅ Встреча <b>{meeting.title}</b> создана!\n"
            f"📅 {meeting.date_time.strftime('%d.%m %H:%M')}\n"
            f"📍 {meeting.address}\n"
            f"👥 {meeting.current_participants}/{meeting.max_participants}"
            f"{age_text}"
        )

        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.exception("[MEETING] Ошибка при создании встречи: %s", e)
        await update.effective_message.reply_text("❌ Ошибка при создании встречи.")

    finally:
        context.user_data.clear()


# --- Чат для встречи ---
async def handle_want_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "chat_no":
        context.user_data['want_chat'] = False
        await create_meeting_now(update, context)
        return ConversationHandler.END

    elif query.data == "chat_yes":
        context.user_data['want_chat'] = True

        try:
            await context.bot.delete_message(
                chat_id=query.message.chat.id,
                message_id=context.user_data['message_id']
            )
        except Exception as e:
            logger.debug(f"[MEETING] Не удалось удалить сообщение: {e}")

        text = (
            "📎 Пришлите ссылку на Telegram-чат:\n"
            "• <code>https://t.me/ваш_чат</code>\n"
            "\n💡 Вы можете отправить ссылку в любой момент."
        )

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 Как создать чат?", callback_data="show_chat_help")]
        ])

        msg = await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup
        )
        context.user_data['message_id'] = msg.message_id
        return WANT_CHAT  # ✅ Остаёмся в WANT_CHAT



async def show_chat_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = (
        "📘 <b>Как создать группу и получить ссылку:</b>\n\n"
        "1. Нажмите ➕ → «Создать группу»\n"
        "2. Добавьте хотя бы одного участника\n"
        "3. Нажмите на название → «Пригласительная ссылка»\n"
        "4. Нажмите «Создать ссылку»\n"
        "5. Скопируйте и отправьте сюда"
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Посмотреть видео", callback_data="send_chat_video")]
    ])

    try:
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup
        )
    except Exception as e:
        # Игнорируем "не изменено", логируем остальное
        if "message is not modified" not in str(e):
            logger.warning(f"Ошибка при редактировании: {e}")



async def send_chat_instruction_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    message_id = query.message.message_id
    key = f"video_sent:{chat_id}:{message_id}"

    # Защита от повторного нажатия
    if context.application.bot_data.get(key):
        await query.answer("Видео уже отправлено", show_alert=True)
        return

    context.application.bot_data[key] = True

    # Убираем кнопки
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        if "message is not modified" not in str(e):
            logger.warning(f"Не удалось убрать кнопки: {e}")

    from constant import VIDEO_CHAT_INSTRUCTION

    try:
        with open(VIDEO_CHAT_INSTRUCTION, "rb") as video:
            await context.bot.send_video(
                chat_id=chat_id,
                video=video,
                caption="Скопируй ссылку на чат и отправь ее мне",
                supports_streaming=True
            )
    except FileNotFoundError:
        await context.bot.send_message(chat_id, "❌ Файл видео не найден на сервере. Обратитесь к администратору.")
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ Ошибка при отправке видео: {e}")

    # Удаляем ключ через 60 секунд — без job_queue
    try:
        loop = asyncio.get_event_loop()
        loop.call_later(60, lambda: context.application.bot_data.pop(key, None))
    except Exception as e:
        logger.debug(f"Не удалось запланировать удаление ключа: {e}")
  # Игнорируем, если не получилось



async def handle_chat_link_anytime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает ссылку на чат в ЛЮБОЙ момент после выбора 'chat_yes'.
    Работает даже если пользователь смотрит инструкцию.
    """
    # Если пользователь не выбрал "да, чат" — игнорируем
    if not context.user_data.get('want_chat'):
        return WANT_CHAT

    link = update.message.text.strip()

    # Проверка формата
    if not (link.startswith("https://t.me/") or link.startswith("@")):
        await update.effective_message.reply_text(
            "❌ Некорректная ссылка. Пример:\n<code>https://t.me/mygroup</code>",
            parse_mode="HTML"
        )
        return WANT_CHAT  # Остаёмся в том же состоянии

    # Сохраняем ссылку
    context.user_data['chat_link'] = link

    # Создаём встречу
    await create_meeting_now(update, context)

    # Завершаем диалог
    return ConversationHandler.END

# --- ConversationHandler ---
meeting_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(create_meeting, pattern="^start_create_meeting$"),
        MessageHandler(
            filters.Regex(r"^(?:\+|➕)?\s*СОЗДАТЬ\s+ВСТРЕЧУ$"),
            create_meeting
        )
    ],
    states={
        MEETING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_title)],
        MEETING_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description)],
        WAITING_PHOTOS: [
            MessageHandler(filters.PHOTO, handle_waiting_photos),
            CallbackQueryHandler(finalize_photos, pattern="^photos_done$"),
            CallbackQueryHandler(handle_waiting_photos, pattern="^(skip_photos|add_photos)$"),
        ],
        MEETING_CATEGORY: [CallbackQueryHandler(handle_category_choice, pattern=r"^category_")],
        MEETING_PRIVACY: [CallbackQueryHandler(handle_privacy_choice, pattern=r"^privacy_")],
        MEETING_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_map_url)],
        MEETING_DATE: [CallbackQueryHandler(handle_date_selection, pattern=r"^cal_")],

        MEETING_TIME: [
            CallbackQueryHandler(handle_time_selection, pattern=r"^(manual_time|time_\d{2}:\d{2})$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_time_selection),
        ],
        MEETING_PARTICIPANTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_max_participants)],
        AGE_RANGE_CHOICE: [CallbackQueryHandler(handle_age_range_choice, pattern=r"^(set_age_range|no_age_limit)$")],
        MIN_AGE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_min_age_input)],
        MAX_AGE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_max_age_input)],
        WANT_CHAT: [
            CallbackQueryHandler(handle_want_chat, pattern=r"^chat_(yes|no)$"),
            CallbackQueryHandler(show_chat_help, pattern="^show_chat_help$"),
            CallbackQueryHandler(send_chat_instruction_video, pattern="^send_chat_video$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat_link_anytime),],
        },
    fallbacks=[CommandHandler("cancel", lambda u, c: c.user_data.clear() or ConversationHandler.END)],
    per_user=True,
    allow_reentry=True,
    name="create_meeting_conv",
    persistent=False,
    block=True,
)
