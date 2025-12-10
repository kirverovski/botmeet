"""
logic.py — бизнес-логика бота
"""
import logging
import calendar
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urlparse, parse_qs, unquote
import re
import aiohttp
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import ContextTypes
from sqlalchemy import select, func
from db import Meeting, User, MeetingParticipant, get_db
from config import YANDEX_API_KEY
from redis_client import redis_client as redis  # Подключаем Redis
import json

logger = logging.getLogger(__name__)

# TTL кэша — 30 дней (в секундах)
_CACHE_TTL = 30 * 24 * 3600  # 30 дней
_MAX_CACHE_SIZE_WARNING = False  # Redis сам управляется по maxmemory


async def get_coordinates_from_cache(address: str) -> Optional[Tuple[float, float]]:
    """
    Получить координаты из Redis.
    Возвращает (lat, lon) или (None, None), если не найдено или ошибка.
    """
    cache_key = f"geocode:{address}"
    try:
        cached = await redis.get(cache_key)
        if cached:
            data = json.loads(cached)
            logger.debug(f"♻️ Кэш Redis: '{address}' → {data['lat']}, {data['lon']}")
            return data["lat"], data["lon"]
    except Exception as e:
        logger.warning(f"⚠️ Ошибка при чтении кэша Redis для '{address}': {e}")
    return None, None


async def cache_coordinates(address: str, lat: float, lon: float):
    """
    Сохранить координаты в Redis с TTL.
    """
    cache_key = f"geocode:{address}"
    try:
        await redis.setex(
            cache_key,
            _CACHE_TTL,
            json.dumps({"lat": lat, "lon": lon}),
        )
        logger.debug(f"💾 Кэш Redis: '{address}' → {lat}, {lon}")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка при записи в Redis кэш '{address}': {e}")


async def get_all_upcoming_meetings(user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Получает все предстоящие встречи.
    Если user_id задан — только те, в которых участвует пользователь.
    """
    async with get_db() as db:
        stmt = select(
            Meeting.id,
            Meeting.title,
            Meeting.address,
            Meeting.latitude,
            Meeting.longitude,
            Meeting.date_time,
            Meeting.max_participants,
            Meeting.current_participants,
            Meeting.creator_id,
        ).where(Meeting.date_time > datetime.now())

        if user_id:
            stmt = stmt.join(MeetingParticipant).where(MeetingParticipant.user_id == user_id)

        result = await db.execute(stmt)
        rows = result.fetchall()

        return [
            {
                "id": row.id,
                "title": row.title,
                "address": row.address,
                "latitude": row.latitude,
                "longitude": row.longitude,
                "date_time": row.date_time,
                "max_participants": row.max_participants,
                "current_participants": row.current_participants,
                "creator_id": row.creator_id,
            }
            for row in rows
        ]


async def is_user_registered(user_id: int) -> bool:
    """
    Проверяет, зарегистрирован ли пользователь по его telegram_id.
    Теперь достаточно просто наличия записи в таблице users.
    """
    async with get_db() as db:
        result = await db.execute(
            select(User).where(User.telegram_id == user_id)
        )
        return result.scalar() is not None


async def is_user_in_meeting(user_id: int, meeting_id: int) -> bool:
    """
    Проверяет, участвует ли пользователь в встрече.
    """
    async with get_db() as db:
        stmt = select(MeetingParticipant).where(
            MeetingParticipant.user_id == user_id,
            MeetingParticipant.meeting_id == meeting_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none() is not None


def get_main_keyboard(registered: bool) -> ReplyKeyboardMarkup:
    keyboard = [
        [
            KeyboardButton("➕ СОЗДАТЬ ВСТРЕЧУ"),
            KeyboardButton("🔍 НАЙТИ ВСТРЕЧУ"),
        ]
    ]
    if registered:
        keyboard.append([KeyboardButton("👥 Мои встречи")])
    else:
        keyboard.append([KeyboardButton("👤 ЗАРЕГИСТРИРОВАТЬСЯ")])
    
    # Добавляем кнопку "Инфо" в отдельной строке
    keyboard.append([KeyboardButton("💡 Инфо")])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)



def extract_coordinates_from_yandex(url: str) -> Optional[Tuple[float, float]]:
    """
    Извлекает координаты из ЛЮБОЙ ссылки Яндекс.Карт:
    - Полная: ...?ll=37.6173,55.7558
    - Поиск: ...?text=улица+Луначарского
    - Сокращённая: .../-/CLcMuTnB
    - Мобильная метка: ...?pt=37.6173,55.7558
    - Начальная точка: ...?sll=39.722172,47.218975

    Возвращает (lat, lon) или None
    """
    try:
        url = url.strip().split("#")[0]  # Убираем хеш
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        # Помощник: возвращает (lat, lon) по строке "lon,lat"
        def parse_lon_lat(s: str) -> Tuple[float, float]:
            lon_str, lat_str = s.replace('%2C', ',').split(',')
            return float(lat_str), float(lon_str)  # (lat, lon)

        # 1. ll=lon,lat
        if 'll' in params:
            return parse_lon_lat(params['ll'][0])

        # 2. pt=lon,lat
        if 'pt' in params:
            return parse_lon_lat(params['pt'][0])

        # 3. sll=lon,lat — стартовая точка
        if 'sll' in params:
            return parse_lon_lat(params['sll'][0])

        # 4. whatshere[point]
        for key in ['whatshere%5Bpoint%5D', 'whatshere[point]']:
            if key in params:
                return parse_lon_lat(params[key][0])

        # 5. text=... — может быть: а) координаты, б) адрес
        if 'text' in params:
            raw_text = params['text'][0]
            decoded = unquote(raw_text)
            # Попробуем как координаты: lat,lon или lon,lat
            coord_match = re.search(r'(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)', decoded)
            if coord_match:
                a, b = map(float, coord_match.groups())
                # Проверим, похожи ли на Россию
                if 40 < a < 80 and 20 < b < 150:  # lat, lon
                    return a, b
                elif 20 < a < 150 and 40 < b < 80:  # lon, lat
                    return b, a
            return None  # → геокодирование

        # 6. Сокращённая ссылка: /-/...
        if re.search(r'maps/\-/([A-Za-z0-9]+)', url):
            logger.info(f"🔗 Обнаружена сокращённая ссылка: {url}")
            return None  # → геокодирование по URL

        logger.warning(f"❌ Не удалось извлечь координаты: {url}")
        return None

    except Exception as e:
        logger.exception(f"❌ Ошибка при парсинге ссылки: {e}")
        return None


async def get_coords_from_yandex(address: str) -> Optional[Tuple[float, float]]:
    """
    Геокодирует адрес через Яндекс.Карты.
    Использует Redis для кэширования.
    """
    if not YANDEX_API_KEY:
        raise ValueError("❌ YANDEX_API_KEY не задан в переменных окружения")

    # Проверяем кэш Redis
    lat, lon = await get_coordinates_from_cache(address)
    if lat is not None and lon is not None:
        return lat, lon

    # Если нет в кэше — запрашиваем
    url = f"https://geocode-maps.yandex.ru/1.x/?apikey={YANDEX_API_KEY}&format=json&geocode={address}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as response:
                response.raise_for_status()
                data = await response.json()

        feature_member = data["response"]["GeoObjectCollection"]["featureMember"]
        if not feature_member:
            logger.warning(f"❌ Нет результатов для адреса: {address}")
            return None

        geo_object = feature_member[0]["GeoObject"]
        pos = geo_object["Point"]["pos"]
        lon, lat = map(float, pos.split())

        # Сохраняем в Redis
        await cache_coordinates(address, lat, lon)

        logger.debug(f"🌐 Новые координаты для '{address}': {lat}, {lon}")
        return lat, lon

    except Exception as e:
        logger.exception(f"❌ Ошибка геокодирования: {e}")
        return None


async def extract_address_from_yandex(map_url: str) -> Optional[str]:
    """
    Извлекает координаты из ссылки и возвращает обратный адрес.
    """
    try:
        parsed = urlparse(map_url)
        params = parse_qs(parsed.query)

        point = None
        for param in ["ll", "pt", "whatshere%5Bpoint%5D", "whatshere[point]"]:
            val = params.get(param, [None])[0]
            if val:
                coords = val.replace('%2C', ',')
                try:
                    lon, lat = map(float, coords.split(','))
                    point = (lat, lon)
                    break
                except ValueError:
                    continue

        if not point:
            logger.warning(f"❌ Не найдены координаты в URL: {map_url}")
            return None

        lat, lon = point
        reverse_url = f"https://geocode-maps.yandex.ru/1.x/?apikey={YANDEX_API_KEY}&format=json&geocode={lon},{lat}"

        async with aiohttp.ClientSession() as session:
            async with session.get(reverse_url, timeout=5) as response:
                data = await response.json()

        feature_member = data["response"]["GeoObjectCollection"]["featureMember"]
        if not feature_member:
            return None

        address = feature_member[0]["GeoObject"]["metaDataProperty"]["GeocoderMetaData"]["text"]

        # Очистка от лишних деталей
        address = re.sub(r'\([^)]*(?:город|область|район|край)[^)]*\)', '', address, flags=re.IGNORECASE).strip()
        if "," in address:
            address = address.split(",", 1)[0].strip()

        return address

    except Exception as e:
        logger.exception(f"❌ Ошибка обратного геокодирования: {e}")
        return None
