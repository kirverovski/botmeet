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

# === Обязательные переменные ===
TELEGRAM_API_KEY = os.getenv("TELEGRAM_API_KEY")
if not TELEGRAM_API_KEY:
    raise RuntimeError("❌ TELEGRAM_API_KEY не задан. Проверьте переменные окружения.")

YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
if not YANDEX_API_KEY:
    raise RuntimeError("❌ YANDEX_API_KEY не задан.")

YANDEX_GPT_API_KEY = os.getenv("YANDEX_GPT_API_KEY")
if not YANDEX_GPT_API_KEY:
    raise RuntimeError("❌ YANDEX_GPT_API_KEY не задан.")

YANDEX_GPT_FOLDER_ID = os.getenv("YANDEX_GPT_FOLDER_ID")
if not YANDEX_GPT_FOLDER_ID:
    raise RuntimeError("❌ YANDEX_GPT_FOLDER_ID не задан.")

# === Вебхук и порт ===
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# === База данных ===
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    if DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
else:
    # Локально: можно использовать sqlite, но лучше PostgreSQL
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, "meetings.db")
    DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

# === Дополнительные настройки ===
WEBAPP_MAP_URL = os.getenv("WEBAPP_MAP_URL", "https://yandex.ru/maps")
YANDEX_GPT_ENABLED = True  # Можно отключить для тестов
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")