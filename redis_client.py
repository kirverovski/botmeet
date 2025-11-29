"""
redis.py — Асинхронное подключение к Redis с использованием официальной библиотеки `redis`
"""
import redis.asyncio as redis
from typing import Optional

# Глобальный клиент Redis
redis_client: Optional[redis.Redis] = None


async def init_redis():
    """
    Инициализация подключения к Redis.
    Вызывается при старте бота.
    """
    global redis_client
    try:
        redis_client = redis.from_url(
            "redis://localhost:6379",  # Убедись, что Redis запущен
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
        await redis_client.aclose()  # Используем aclose() для корректного завершения
        print("✅ Redis: соединение закрыто")
