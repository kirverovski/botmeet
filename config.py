"""
config.py — централизованная конфигурация бота
Поддержка: .env, облако (Render, Railway), Windows, Linux
"""
import os
import logging
from dotenv import load_dotenv

# === Настройка логирования ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Загружаем .env (если файл существует)
if os.path.exists(".env"):
    load_dotenv()
    logger.info("🔐 .env загружен локально")

# === Обязательные переменные ===
TELEGRAM_API_KEY = os.getenv("TELEGRAM_API_KEY")
if not TELEGRAM_API_KEY:
    raise ValueError("❌ ОШИБКА: TELEGRAM_API_KEY не задан")

YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
if not YANDEX_API_KEY:
    raise ValueError("❌ ОШИБКА: YANDEX_API_KEY не задан")

YANDEX_GPT_API_KEY = os.getenv("YANDEX_GPT_API_KEY")
if not YANDEX_GPT_API_KEY:
    raise ValueError("❌ ОШИБКА: YANDEX_GPT_API_KEY не задан")

YANDEX_GPT_FOLDER_ID = os.getenv("YANDEX_GPT_FOLDER_ID")
if not YANDEX_GPT_FOLDER_ID:
    raise ValueError("❌ ОШИБКА: YANDEX_GPT_FOLDER_ID не задан")

# === Вебхук и порт ===
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Например: https://your-bot.onrender.com

# === База данных ===
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    # Если указано — заменяем драйвер для PostgreSQL
    if DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
        logger.info("☁️ Используется облачная БД (PostgreSQL)")
    else:
        logger.info(f"🔧 Используется БД: {DATABASE_URL.split('://')[0]}")
else:
    # Локально: принудительно используем aiosqlite
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, "meetings.db")
    DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"
    logger.info("📌 Используется локальная БД: meetings.db")

# Логируем с маскировкой
safe_url = DATABASE_URL
if "://" in safe_url:
    auth_start = safe_url.find("://") + 3
    auth_end = safe_url.find("@")
    if auth_end > auth_start:
        safe_url = safe_url[:auth_start] + "***:***@" + safe_url[auth_end + 1:]
logger.info(f"🔌 DATABASE_URL: {safe_url}")

# === Дополнительные настройки ===
WEBAPP_MAP_URL = os.getenv("WEBAPP_MAP_URL", "https://yandex.ru/maps")
YANDEX_GPT_ENABLED = True  # Можно отключить для тестов
