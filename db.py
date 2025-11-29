"""
db.py — Асинхронная работа с базой данных (PostgreSQL)
Исправлено: id → BigInteger для поддержки больших Telegram ID
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

# === Импорты типов SQLAlchemy ===
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Numeric,
    BigInteger,
    UniqueConstraint,
    func,
    select,
    text,
    ForeignKey,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base, relationship

from config import DATABASE_URL

# Логгер
logger = logging.getLogger(__name__)

# База для моделей
Base = declarative_base()

# === Асинхронный engine с пулом соединений ===
try:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,  # Включить для отладки: True
        pool_size=20,           # Основные соединения
        max_overflow=40,        # Дополнительные при пике
        pool_pre_ping=True,     # Проверять соединение перед использованием
        pool_recycle=3600,      # Пересоздавать соединения каждые 3600 сек (1 час)
        pool_timeout=30,        # Ожидание соединения
    )

    # Фабрика сессий
    AsyncSessionLocal = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    logger.info("✅ Асинхронный engine создан и настроен.")
    logger.info(f"📍 Используется БД: {DATABASE_URL.replace('//', '://***:***@')}")

except Exception as e:
    logger.exception("❌ Критическая ошибка при создании engine: %s", e)
    raise


# === Инициализация БД ===
async def init_db():
    """Создаёт таблицы, если их нет"""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Таблицы проверены и созданы (если не существовали)")
    except Exception as e:
        logger.exception("❌ Ошибка при инициализации БД: %s", e)
        raise


# === Управление сессией ===
@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Контекстный менеджер для безопасной работы с сессией БД.
    Гарантирует: commit, rollback, закрытие.
    """
    session = AsyncSessionLocal()
    try:
        yield session
        await session.commit()
    except Exception as e:
        await session.rollback()
        logger.error("❌ Ошибка в транзакции БД: %s", e)
        raise
    finally:
        await session.close()


# === МОДЕЛИ (исправленные) ===

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True, index=True)  # ✅ BigInteger!
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String(100), nullable=True)
    full_name = Column(String(100), nullable=False)
    age = Column(Integer, nullable=False)
    gender = Column(String(10), nullable=False)
    about = Column(Text, nullable=True)
    photo_id = Column(String(200), nullable=True)
    registration_step = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=func.now())

    participations = relationship("MeetingParticipant", back_populates="user", cascade="all, delete-orphan")


class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(BigInteger, primary_key=True, index=True)  # ✅ BigInteger!
    title = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(50), nullable=False)
    privacy = Column(String(10), nullable=False)
    latitude = Column(Numeric(9, 6), nullable=False)
    longitude = Column(Numeric(9, 6), nullable=False)
    address = Column(String(200), nullable=False)
    date_time = Column(DateTime, nullable=False)
    max_participants = Column(Integer, nullable=False)
    min_age = Column(Integer, nullable=True)
    max_age = Column(Integer, nullable=True)
    chat_link = Column(String(200), nullable=True)
    photos_data = Column(Text, nullable=True)
    current_participants = Column(Integer, default=1)
    creator_id = Column(BigInteger, nullable=False)  # Это telegram_id пользователя
    is_approved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())

    # Важно: creator_id — это telegram_id, а не User.id
    creator = relationship(
        "User",
        foreign_keys=[creator_id],
        primaryjoin="User.telegram_id==Meeting.creator_id",
        viewonly=True  # Только для чтения, не влияет на FK
    )
    participants = relationship("MeetingParticipant", back_populates="meeting", cascade="all, delete-orphan")


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"
    id = Column(BigInteger, primary_key=True, index=True)  # ✅ BigInteger!
    meeting_id = Column(BigInteger, ForeignKey("meetings.id"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False, index=True)
    joined_at = Column(DateTime, default=func.now())

    __table_args__ = (
        UniqueConstraint('meeting_id', 'user_id', name='_meeting_user_uc'),
    )

    meeting = relationship("Meeting", back_populates="participants")
    user = relationship("User", back_populates="participations")
