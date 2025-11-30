"""
redis_client.py — Асинхронное подключение к Redis с использованием переменной окружения
"""
import os
import redis.asyncio as redis
from typing import Optional

# Глобальный клиент Redis
redis_client: Optional[redis.Redis] = None


async def init_redis():
    """
    Инициализация подключения к Redis.
    Берёт URL из переменной окружения REDIS_URL.
    """
    global redis_client
    try:
        # Читаем URL из переменной окружения
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        redis_client = redis.from_url(
            redis_url,
            encoding="utf8",
            decode_responses=True,  # Чтобы возвращались строки, а не байты
        )
        # Проверка подключения
        await redis_client.ping()
        print("✅ Redis: подключение установлено")
    except Exception as e:
        print(f"❌ Не удалось подключиться к Redis: {e}")
        raise
    return redis_client


async def close_redis():
    """
    Закрытие подключения к Redis.
    Вызывается при остановке бота.
    """
    global redis_client
    if redis_client:
        await redis_client.aclose()
        print("✅ Redis: соединение закрыто")
