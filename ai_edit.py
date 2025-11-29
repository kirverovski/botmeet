
import aiohttp
import asyncio
import logging
from typing import Optional, Dict

from config import YANDEX_GPT_API_KEY, YANDEX_GPT_FOLDER_ID
from constant import MEETING_CATEGORIES

logger = logging.getLogger(__name__)


async def ask_gpt(system: str, user: str) -> Optional[str]:
    """
    Простой вызов GPT. Возвращает текст или None.
    """
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "modelUri": f"gpt://{YANDEX_GPT_FOLDER_ID}/yandexgpt-lite/latest",
        "completion_options": {"temperature": 0.7, "maxTokens": "300"},
        "messages": [
            {"role": "system", "text": system},
            {"role": "user", "text": user},
        ],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.warning(f"[AI] GPT error {resp.status}: {error}")
                    return None
                data = await resp.json()
                return data["result"]["alternatives"][0]["message"]["text"].strip()
    except Exception as e:
        logger.warning(f"[AI] Ошибка при обращении к GPT: {e}")
        return None


async def improve_title(title: str, instruction: str = "") -> Optional[str]:
    """
    Улучшает название встречи.
    """
    system = (
        "Ты — редактор названий для встреч. "
        "Сделай его ярким, кратким и понятным. "
        "Максимум 100 символов. Только само название — без кавычек и пояснений."
    )
    user = f"Название: {title}\nИнструкция: {instruction if instruction else 'сделай привлекательнее'}"
    result = await ask_gpt(system, user)
    return result[:100] if result else None


async def improve_description(title: str, category: str, current: str, instruction: str = "") -> Optional[str]:
    """
    Улучшает описание встречи.
    """
    system = (
        "Ты — копирайтер для соцсетей. Напиши дружелюбное, живое описание встречи. "
        "Максимум 500 символов. Только текст — без префиксов."
    )
    user = (
        f"Категория: {category}\n"
        f"Название: {title}\n"
        f"Текущее описание: {current or 'нет'}\n"
        f"Инструкция: {instruction if instruction else 'сделай дружелюбнее'}"
    )
    result = await ask_gpt(system, user)
    return result[:500] if result else None


async def suggest_category(title: str, description: str) -> Optional[str]:
    """
    Предлагает подходящую категорию.
    """
    system = f"Выбери одну категорию из списка: {', '.join(MEETING_CATEGORIES)}. Только название — без пояснений."
    user = f"Название: {title}\nОписание: {description}\nКакая категория подходит лучше всего?"
    result = await ask_gpt(system, user)
    return result if result in MEETING_CATEGORIES else None


async def suggest_age_range(description: str) -> Optional[Dict[str, int]]:
    """
    Предлагает возрастной диапазон.
    Возвращает: {"min_age": 18, "max_age": 35} или None
    """
    system = (
        "Проанализируй описание встречи. Предложи возрастной диапазон.\n"
        "Верни только строку в формате: min=18, max=35\n"
        "Если не понятно — верни: min=0, max=0"
    )
    user = f"Описание встречи: {description}"
    result = await ask_gpt(system, user)

    try:
        if not result or "min=0, max=0" in result:
            return None
        # Парсим: min=18, max=35
        parts = result.replace(" ", "").split(",")
        min_age = int(parts[0].split("=")[1])
        max_age = int(parts[1].split("=")[1])
        if 1 <= min_age <= 120 and min_age <= max_age <= 120:
            return {"min_age": min_age, "max_age": max_age}
    except Exception as e:
        logger.debug(f"[AI] Не удалось распарсить возраст: {result}, ошибка: {e}")
        return None

    return None

def get_handlers():
    from edit_meeting import handle_ai_instruction

    return {
        "handle_ai_edit_message": handle_ai_instruction,
        "handle_ai_edit_finalize": lambda u, c: None,  # Заглушка — не используется
    }