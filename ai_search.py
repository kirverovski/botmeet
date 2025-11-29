"""
ai_search.py — модуль для ИИ-поиска встреч через YandexGPT
"""
import aiohttp
import asyncio
import logging
from typing import Optional, List
from datetime import datetime

from config import YANDEX_GPT_API_KEY, YANDEX_GPT_FOLDER_ID
from db import get_db, Meeting, MeetingParticipant
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes
from constant import JOIN_PREFIX, LEAVE_PREFIX
from logic import is_user_registered

logger = logging.getLogger(__name__)


async def call_gpt(prompt: str) -> str:
    """
    Универсальный вызов YandexGPT.
    Возвращает текстовый ответ или "null" при ошибке.
    """
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "modelUri": f"gpt://{YANDEX_GPT_FOLDER_ID}/yandexgpt-lite/latest",
        "completion_options": {
            "temperature": 0.5,
            "maxTokens": "500",
        },
        "messages": [
            {
                "role": "system",
                "text": (
                    "Ты — помощник бота для встреч. Отвечай на русском. Будь краток и точен. "
                    "Не добавляй комментариев. Только то, что просят."
                ),
            },
            {"role": "user", "text": prompt},
        ],
    }

    try:
        logger.debug(f"[AI] Отправляю промпт в YandexGPT: {prompt[:150]}...")
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.warning(f"[AI] GPT вернул {resp.status}: {error_text}")
                    return "null"

                data = await resp.json()

                # Проверка структуры
                try:
                    text = data["result"]["alternatives"][0]["message"]["text"].strip()
                    logger.debug(f"[AI] Ответ получен: {text}")
                    return text
                except (KeyError, IndexError) as e:
                    logger.error(f"[AI] Некорректный формат ответа: {data}")
                    return "null"

    except asyncio.TimeoutError:
        logger.error("[AI] ⏱️ Таймаут при обращении к YandexGPT")
        return "null"
    except Exception as e:
        logger.exception(f"[AI] ❌ Ошибка при вызове GPT: {e}")
        return "null"


async def search_meetings_by_ai(
    query: str, categories: Optional[List[str]] = None
) -> List[int]:
    """
    Ищет ID встреч по текстовому запросу пользователя.
    Поддерживает фильтрацию по категориям.
    Возвращает список ID (может быть пустым).
    """
    if not query.strip():
        logger.warning("[AI] Пустой запрос — возвращаем пустой результат")
        return []

    async with get_db() as db:
        # Фильтр: будущие встречи + по категориям
        stmt = select(Meeting).where(Meeting.date_time > datetime.now())
        if categories and len(categories) > 0:
            stmt = stmt.where(Meeting.category.in_(categories))
        result = await db.execute(stmt)
        meetings = result.scalars().all()

    if not meetings:
        logger.info("[AI] Нет активных встреч для анализа")
        return []

    # Формируем список для GPT
    meetings_list = "\n".join([
        f"ID: {m.id} | Название: {m.title} | Адрес: {m.address} | "
        f"Категория: {m.category} | Описание: {m.description or 'отсутствует'}"
        for m in meetings
    ])

    prompt = (
        "Проанализируй список встреч и определи, какие подходят под запрос пользователя.\n"
        "Верни ТОЛЬКО список ID подходящих встреч ЧЕРЕЗ ЗАПЯТУЮ, без пояснений.\n"
        "Пример: 13, 4, 21\n"
        "Если нет подходящих — верни пустую строку.\n\n"
        f"Запрос пользователя: {query}\n\n"
        f"Список встреч:\n{meetings_list}"
    )

    response = await call_gpt(prompt)

    # Парсинг ответа
    ids = []
    for token in response.split(","):
        token = token.strip()
        if token.isdigit():
            ids.append(int(token))
        elif token:  # Логируем странные токены
            logger.debug(f"[AI] Пропущен токен при парсинге ID: '{token}'")

    # Убираем дубли и фильтруем по существующим ID
    existing_ids = {m.id for m in meetings}
    filtered_ids = list(set(ids) & existing_ids)

    logger.info(f"[AI] Найдено {len(filtered_ids)} подходящих встреч: {filtered_ids}")
    return filtered_ids


# ❌ УДАЛЁН handle_ai_search — дублирует логику searchmeetings.py
# Оставь только если используешь команду /search отдельно

