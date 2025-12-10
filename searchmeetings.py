from telegram import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Update,
    InputMediaPhoto,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from datetime import datetime
from typing import Optional, List
import math
import logging
import aiohttp
import json
from db import get_db, Meeting, MeetingParticipant, User
from sqlalchemy import select
from config import YANDEX_API_KEY
from constant import MEETING_CATEGORIES, JOIN_PREFIX, LEAVE_PREFIX
from logic import extract_coordinates_from_yandex, get_main_keyboard, is_user_registered
from common import send_main_menu

logger = logging.getLogger(__name__)

def can_user_see_meeting(user_gender: str, meeting_required_gender: Optional[str]) -> bool:
    logger.info(f"[GENDER_FILTER] 🚻 Пользователь: '{user_gender}' (type={type(user_gender)}), Встреча: '{meeting_required_gender}' (type={type(meeting_required_gender)})")

    if not meeting_required_gender or not str(meeting_required_gender).strip():
        logger.info("[GENDER_FILTER] → required_gender пустой → ✅ разрешено")
        return True

    required_str = str(meeting_required_gender).strip()

    if "Любой" in required_str:
        logger.info("[GENDER_FILTER] → 'Любой' найден → ✅ разрешено")
        return True

    allowed_genders = {g.strip() for g in required_str.split(",") if g.strip()}
    logger.info(f"[GENDER_FILTER] → Допустимые полы: {allowed_genders}")

    if user_gender in allowed_genders:
        logger.info(f"[GENDER_FILTER] ✅ '{user_gender}' разрешён")
        return True
    else:
        logger.info(f"[GENDER_FILTER] ❌ '{user_gender}' не входит в {allowed_genders}")
        return False

def can_user_join_by_age(user_age: int, min_age: Optional[int], max_age: Optional[int]) -> bool:
    """
    Проверяет, может ли пользователь участвовать в встрече по возрасту.
    """
    if min_age is not None and user_age < min_age:
        return False
    if max_age is not None and user_age > max_age:
        return False
    return True

async def handle_find_meetings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_user_registered(user_id):
        await update.message.reply_text("⚠️ Пройдите регистрацию, чтобы искать встречи.")
        await send_main_menu(update, context)
        return

    # Состояние
    context.user_data["awaiting_category_selection"] = True
    if "selected_categories" not in context.user_data:
        context.user_data["selected_categories"] = set()

    # Кнопки категорий
    buttons = [
        [InlineKeyboardButton(f"⬜ {cat}", callback_data=f"cat_{cat}")]
        for cat in MEETING_CATEGORIES
    ]

    # Кнопки управления
    buttons.append([
        InlineKeyboardButton("✅ Готово", callback_data="cat_done"),
        InlineKeyboardButton("⏭️ Пропустить", callback_data="cat_skip"),
    ])

    markup = InlineKeyboardMarkup(buttons)

    msg_text = (
        "🔍 <b>Выберите интересующие вас категории:</b>\n\n"
        "🔹 Нажмите на категорию, чтобы отметить/снять\n"
        "🔹 Нажмите «Готово», чтобы продолжить\n"
        "🔹 Можно выбрать несколько — или пропустить"
    )

    await update.message.reply_text(
        msg_text,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )


async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка выбора категорий.
    """
    query = update.callback_query
    data = query.data

    if not context.user_data.get("awaiting_category_selection"):
        await query.answer()
        return

    selected = context.user_data["selected_categories"]
    category = data[4:] if data.startswith("cat_") else None

    # --- Обработка: Готово ---
    if data == "cat_done":
        await query.answer(f"Выбрано: {len(selected)} категорий")
        context.user_data["awaiting_category_selection"] = False

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📍 Поблизости", callback_data="near_me")],
            [InlineKeyboardButton("🤖 Поиск через ИИ", callback_data="ai_search")],
        ])

        text = f"✅ Выбрано: {len(selected)} категорий\n\nВыберите способ поиска:"
        await query.edit_message_text(text=text, reply_markup=markup, parse_mode=ParseMode.HTML)
        return

    # --- Обработка: Пропустить ---
    if data == "cat_skip":
        await query.answer("Пропущено")
        context.user_data["selected_categories"] = []  # ✅ Пустой список, не None
        context.user_data["skip_categories"] = True   # ✅ Флаг: категории пропущены
        context.user_data["awaiting_category_selection"] = False

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📍 Поблизости", callback_data="near_me")],
            [InlineKeyboardButton("🤖 Поиск через ИИ", callback_data="ai_search")],
        ])

        await query.edit_message_text(
            text="Выберите способ поиска:",
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
        return

    # --- Обработка: Переключение категории ---
    if category and category in MEETING_CATEGORIES:
        if category in selected:
            selected.discard(category)
            emoji = "⬜"
        else:
            selected.add(category)
            emoji = "✅"

        # Обновляем кнопки
        buttons = [
            [InlineKeyboardButton(f"{'✅' if cat in selected else '⬜'} {cat}", callback_data=f"cat_{cat}")]
            for cat in MEETING_CATEGORIES
        ]
        buttons.append([
            InlineKeyboardButton("✅ Готово", callback_data="cat_done"),
            InlineKeyboardButton("⏭️ Пропустить", callback_data="cat_skip"),
        ])
        markup = InlineKeyboardMarkup(buttons)

        await query.answer()
        await query.edit_message_reply_markup(reply_markup=markup)

async def request_ai_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Запрос описания встречи у пользователя для поиска через ИИ.
    """
    query = update.callback_query
    await query.answer()

    # Убираем кнопки
    await query.edit_message_reply_markup(reply_markup=None)

    # Инструкция
    msg_text = (
        "🤖 <b>Опишите, какую встречу вы ищете</b>\n\n"
        "Примеры:\n"
        "• Пробежка и общение\n"
        "• Встреча для фрилансеров\n"
        "• Кофе и знакомства в центре\n"
        "• Групповое чтение книг"
    )

    await query.message.reply_text(msg_text, parse_mode=ParseMode.HTML)

    # Устанавливаем состояние
    context.user_data["awaiting_ai_query"] = True


async def handle_ai_query_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка ввода пользователя для ИИ-поиска с фильтрацией по полу и возрасту.
    """
    if not context.user_data.get("awaiting_ai_query"):
        return

    query_text = update.message.text.strip()
    user_id = update.effective_user.id

    if not query_text:
        await update.message.reply_text("📝 Пожалуйста, опишите интересующую встречу.")
        return

    context.user_data["awaiting_ai_query"] = False
    await update.message.reply_text("🔍 Ищу подходящие встречи с помощью ИИ...")

    # Категории
    if context.user_data.get("skip_categories"):
        categories = None
    else:
        selected = context.user_data.get("selected_categories", [])
        categories = list(selected) if selected else None

    try:
        from ai_search import search_meetings_by_ai
        meeting_ids = await search_meetings_by_ai(query_text, categories=categories)

        if not meeting_ids:
            await update.message.reply_text("😔 Не нашлось подходящих встреч по вашему запросу.")
            return

        async with get_db() as db:
            result = await db.execute(select(Meeting).where(Meeting.id.in_(meeting_ids)))
            meetings = result.scalars().all()

        if not meetings:
            await update.message.reply_text("😔 Встречи не найдены.")
            return

        # Логируем наличие встреч до фильтрации
        logger.info(f"[AI_SEARCH] Найдено встреч до фильтрации: {len(meetings)}")

        # Получаем пол и возраст пользователя
        result = await db.execute(
            select(User.gender, User.age).where(User.telegram_id == user_id)
        )
        user_gender, user_age = result.first()

        if not user_gender:
            await update.message.reply_text("❌ Для поиска необходимо указать ваш пол.")
            return
        if not user_age:
            await update.message.reply_text("❌ Для поиска необходимо указать ваш возраст.")
            return

        logger.info(f"[AI_SEARCH] Пользователь {user_id} — пол: {user_gender}, возраст: {user_age}")

        # Фильтрация по полу
        meetings_before = len(meetings)
        meetings = [m for m in meetings if can_user_see_meeting(user_gender, m.required_gender)]
        logger.info(f"[AI_SEARCH] После фильтрации по полу: {meetings_before} → {len(meetings)}")

        if not meetings:
            await update.message.reply_text("😔 Нет подходящих встреч по вашему полу.")
            return

        # ✅ Фильтрация по возрасту
        meetings_before_age = len(meetings)
        meetings = [m for m in meetings if can_user_join_by_age(user_age, m.min_age, m.max_age)]
        logger.info(f"[AI_SEARCH] После фильтрации по возрасту: {meetings_before_age} → {len(meetings)}")

        if not meetings:
            await update.message.reply_text("😔 Нет подходящих встреч по вашему возрасту.")
            return

        # Получаем ID встреч, в которых пользователь участвует
        result = await db.execute(
            select(MeetingParticipant.meeting_id).where(MeetingParticipant.user_id == user_id)
        )
        user_participations = set(result.scalars().all())

        # Отображение каждой встречи
        for meeting in meetings:
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

            is_creator = meeting.creator_id == user_id
            is_joined = meeting.id in user_participations

            if is_creator:
                buttons = [
                    [InlineKeyboardButton("✅ Это ваша встреча", callback_data="own_meeting")],
                    [InlineKeyboardButton("🔍 Подробнее", callback_data=f"details_{meeting.id}")]
                ]
            else:
                buttons = [
                    [
                        InlineKeyboardButton(
                            "✅ Покинуть" if is_joined else "✅ Присоединиться",
                            callback_data=f"{LEAVE_PREFIX if is_joined else JOIN_PREFIX}{meeting.id}"
                        ),
                        InlineKeyboardButton("🔍 Подробнее", callback_data=f"details_{meeting.id}")
                    ]
                ]

            markup = InlineKeyboardMarkup(buttons)

            # Обработка фото
            if meeting.photos_data:
                try:
                    photos = json.loads(meeting.photos_data)
                    if photos:
                        media_group = [InputMediaPhoto(media=p['file_id']) for p in photos]
                        await context.bot.send_media_group(chat_id=update.effective_chat.id, media=media_group)
                        await update.effective_message.reply_text(
                            text=text,
                            reply_markup=markup,
                            parse_mode=ParseMode.HTML
                        )
                        continue
                except Exception as e:
                    logger.warning(f"[PHOTO] Ошибка при отправке фото встречи {meeting.id}: {e}")

            await update.effective_message.reply_text(
                text=text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML
            )

        # Финальное сообщение
        await update.message.reply_text("Вот что нашёл ИИ 🤖")

    except Exception as e:
        logger.exception("[AI_SEARCH] Неожиданная ошибка при поиске встреч: %s", e)
        await update.message.reply_text("❌ Произошла ошибка при поиске. Попробуйте позже.")



async def request_location(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if query:
        await query.answer()

        # Удаляем inline-кнопки, НЕ заменяя текст
        await query.edit_message_reply_markup(reply_markup=None)

    # Создаём клавиатуру с кнопкой "Отправить геопозицию"
    reply_markup = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Отправить геопозицию", request_location=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
        selective=True
    )

    # Отправляем ОДНО сообщение с текстом и клавиатурой
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🌍 Поделитесь своим местоположением, чтобы найти встречи поблизости:",
        reply_markup=reply_markup,
        disable_notification=True
    )

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.location:
        return

    # Убираем клавиатуру
    from telegram import ReplyKeyboardRemove
    await update.message.reply_text(
        "🔍 Определяем ваш город...",
        reply_markup=ReplyKeyboardRemove()
    )

    lat = update.message.location.latitude
    lon = update.message.location.longitude

    city = await get_city_from_coords(lat, lon)
    if not city or city == "Неизвестный город":
        await update.message.reply_text("❌ Не удалось определить город. Попробуйте ещё раз.")

        # ✅ Получаем user_id
        user_id = update.effective_user.id
        registered = await is_user_registered(user_id)

        await update.effective_message.reply_text(
            "Что дальше?",
            reply_markup=get_main_keyboard(registered=registered)
        )
        return

    context.user_data.update({
        "step": "near_me",
        "city": city,
        "lat": lat,
        "lon": lon,
    })

    # Показ встреч
    await show_near_me_meetings(update, context, lat, lon, page=0)

    # ✅ Получаем user_id
    user_id = update.effective_user.id
    registered = await is_user_registered(user_id)

    # ✅ Отправляем меню как ответ
    await update.effective_message.reply_text(
        "🔚 Что хотите сделать дальше?",
        reply_markup=get_main_keyboard(registered=registered)
    )





async def get_city_from_coords(lat: float, lon: float) -> str:
    """
    Определяет город по координатам через Yandex Geocoder.
    """
    url = f"https://geocode-maps.yandex.ru/1.x/?apikey={YANDEX_API_KEY}&format=json&geocode={lon},{lat}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=8) as response:
                if response.status != 200:
                    return "Неизвестный город"
                data = await response.json()

        feature = data["response"]["GeoObjectCollection"]["featureMember"][0]["GeoObject"]
        address = feature["metaDataProperty"]["GeocoderMetaData"]["text"]
        parts = [p.strip() for p in address.split(",")]

        # Ищем часть, похожую на город
        for p in parts:
            p_lower = p.lower()
            if any(kw in p_lower for kw in ["район", "область", "улица", "проспект", "шоссе", "переулок", "дом", "стр", "кв"]):
                continue
            if len(p) > 2 and p[0].isupper() and not p.isdigit():
                return p

        # Обратный поиск
        for p in reversed(parts):
            if len(p) > 2 and p[0].isupper() and not any(kw in p_lower for kw in ["обл", "ул", "пр", "ш", "д"]):
                return p

        return parts[1] if len(parts) > 1 else parts[0]

    except (IndexError, KeyError, aiohttp.ClientError) as e:
        logger.warning(f"[GEO] Не удалось определить город: {e}")
        return "Неизвестный город"


def calculate_distance(lat1: float, lon1: float, lat2, lon2) -> float:
    """
    Вычисляет расстояние между двумя точками (в км)
    lat1, lon1 — пользователь (float)
    lat2, lon2 — встреча (может быть Decimal)
    """
    # Явно конвертируем Decimal → float
    lat2 = float(lat2)
    lon2 = float(lon2)

    from math import radians, sin, cos, sqrt, atan2

    R = 6371.0  # Радиус Земли в км

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    distance = R * c
    return distance


async def get_meetings_by_geo(
    lat: float, lon: float, page: int = 0, per_page: int = 3, categories: Optional[List[str]] = None
) -> List[Meeting]:
    """
    Получение встреч поблизости с фильтрацией по категориям и сортировкой по расстоянию.
    """
    async with get_db() as db:
        stmt = select(Meeting).where(Meeting.date_time > datetime.now())
        if categories:
            stmt = stmt.where(Meeting.category.in_(categories))
        result = await db.execute(stmt)
        meetings = result.scalars().all()

    for m in meetings:
        m.distance = calculate_distance(lat, lon, m.latitude, m.longitude)

    sorted_meetings = sorted(meetings, key=lambda x: x.distance)
    start_idx = page * per_page
    return sorted_meetings[start_idx:start_idx + per_page]

async def show_near_me_meetings(
    update: Update, context: ContextTypes.DEFAULT_TYPE, lat: float, lon: float, page: int = 0):
    
    # ✅ Категории
    if context.user_data.get("skip_categories"):
        categories = None
    else:
        selected = context.user_data.get("selected_categories", [])
        categories = list(selected) if selected else None

    user_id = update.effective_user.id

    # Получаем **на 1 больше**, чем нужно, чтобы проверить, есть ли следующая страница
    meetings = await get_meetings_by_geo(lat, lon, page, per_page=4, categories=categories)

    if not meetings:
        await update.effective_message.reply_text("😔 Встреч поблизости не найдено.")
        return

    # ✅ Получаем пол и возраст пользователя
    async with get_db() as db:
        result = await db.execute(
            select(User.gender, User.age).where(User.telegram_id == user_id)
        )
        user_gender, user_age = result.first()

        if not user_gender:
            await update.message.reply_text("❌ Не удалось определить ваш пол.")
            return
        if not user_age:
            await update.message.reply_text("❌ Не удалось определить ваш возраст.")
            return

    logger.info(f"[NEAR_ME] Пользователь {user_id} — пол: {user_gender}, возраст: {user_age}")

    # ✅ Фильтруем по полу
    meetings = [m for m in meetings if can_user_see_meeting(user_gender, m.required_gender)]

    if not meetings:
        await update.effective_message.reply_text("😔 Нет подходящих встреч по вашему полу.")
        return

    # ✅ Фильтруем по возрасту
    meetings = [m for m in meetings if can_user_join_by_age(user_age, m.min_age, m.max_age)]

    if not meetings:
        await update.effective_message.reply_text("😔 Нет подходящих встреч по вашему возрасту.")
        return

    # ✅ Получаем ID встреч, в которых пользователь участвует
    result = await db.execute(
        select(MeetingParticipant.meeting_id).where(MeetingParticipant.user_id == user_id)
    )
    user_participations = set(result.scalars().all())

    # Ограничиваем до 3
    current_meetings = meetings[:3]
    has_next_page = len(meetings) > 3  # Если вернули 4, значит, есть ещё

    # Отображение
    for meeting in current_meetings:
        free = meeting.max_participants - meeting.current_participants
        status_text = (
            f"🟢 Свободно {free} {['место', 'места', 'мест'][min(free, 3) - 1]} из {meeting.max_participants}"
            if free > 0 else "🔴 Нет свободных мест"
        )

        if meeting.distance < 1.0:
            meters = int(meeting.distance * 1000)
            distance_text = f"{meters} м"
        else:
            distance_text = f"{meeting.distance:.1f} км"

        text = (
            f"📌 <b>{meeting.title}</b>\n"
            f"📅 {meeting.date_time.strftime('%d.%m %H:%M')}\n"
            f"📍 {meeting.address} (<i>{distance_text}</i>)\n"
            f"{status_text}"
        )
        if meeting.description:
            text += f"\n\n{meeting.description}"

        is_creator = meeting.creator_id == user_id
        is_joined = meeting.id in user_participations

        if is_creator:
            buttons = [
                [InlineKeyboardButton("✅ Это ваша встреча", callback_data="own_meeting")],
                [InlineKeyboardButton("🔍 Подробнее", callback_data=f"details_{meeting.id}")]
            ]
        else:
            buttons = [
                [
                    InlineKeyboardButton(
                        "✅ Покинуть" if is_joined else "✅ Присоединиться",
                        callback_data=f"{LEAVE_PREFIX if is_joined else JOIN_PREFIX}{meeting.id}"
                    ),
                    InlineKeyboardButton("🔍 Подробнее", callback_data=f"details_{meeting.id}")
                ]
            ]

        markup = InlineKeyboardMarkup(buttons)

        if meeting.photos_data:
            try:
                photos = json.loads(meeting.photos_data)
                if photos:
                    media_group = [InputMediaPhoto(media=p['file_id']) for p in photos]
                    await context.bot.send_media_group(chat_id=update.effective_chat.id, media=media_group)
                    await update.effective_message.reply_text(
                        text=text, reply_markup=markup, parse_mode=ParseMode.HTML
                    )
                    continue
            except Exception as e:
                logger.warning(f"[PHOTO] Ошибка фото встречи {meeting.id}: {e}")

        await update.effective_message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

    # Кнопка "Показать ещё" — только если есть ещё
    if has_next_page:
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("➡️ Показать ещё 3 встречи", callback_data=f"show_more_near_{page + 1}")]
        ])
        await update.effective_message.reply_text("Хотите увидеть ещё?", reply_markup=markup)


async def handle_show_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка кнопки 'Показать ещё'.
    """
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("show_more_near_"):
        page = int(data.split("_")[-1])
        lat = context.user_data.get("lat")
        lon = context.user_data.get("lon")
        if lat and lon:
            await show_near_me_meetings(update, context, lat, lon, page=page)
        else:
            await query.message.reply_text("❌ Ошибка: координаты утеряны.")

async def handle_near_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка кнопки 'Поблизости'.
    """
    query = update.callback_query
    await query.answer()
    await request_location(update, context)


def get_handlers():
    """
    Возвращает словарь хендлеров для регистрации в main.py.
    """
    return {
        "handle_find_meetings": handle_find_meetings,
        "handle_category_selection": handle_category_selection,
        "request_ai_search": request_ai_search,
        "handle_ai_query_input": handle_ai_query_input,
        "request_location": request_location,
        "handle_location": handle_location,
        "handle_near_me": handle_near_me,
        "handle_show_more": handle_show_more,
    }
