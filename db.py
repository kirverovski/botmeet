import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
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

logger = logging.getLogger(__name__)

Base = declarative_base()

# === Асинхронный engine с пулом соединений ===
try:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False, 
        pool_size=20,
        max_overflow=40,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_timeout=30,
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
    """Создаёт таблицы и добавляет недостающие колонки"""
    try:
        async with engine.begin() as conn:
            # Создаём таблицы, если их ещё нет
            await conn.run_sync(Base.metadata.create_all)
            logger.info("✅ Таблицы проверены и созданы (если не существовали)")

            # Добавляем колонку required_gender, если её нет
            await conn.execute(
                text("""
                ALTER TABLE meetings 
                ADD COLUMN IF NOT EXISTS required_gender VARCHAR(50);
                """)
            )
            logger.info("🔧 Колонка required_gender добавлена (если была отсутствует)")

    except Exception as e:
        logger.exception("❌ Ошибка при инициализации БД: %s", e)
        raise


# === Управление сессией ===
@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
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

# === МОДЕЛИ ===
class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String(100), nullable=True)
    full_name = Column(String(100), nullable=False)
    age = Column(Integer, nullable=False)
    gender = Column(String(10), nullable=False)
    about = Column(Text, nullable=True)
    registration_step = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=func.now())

    participations = relationship("MeetingParticipant", back_populates="user", cascade="all, delete-orphan")

class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(BigInteger, primary_key=True, index=True)
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
    creator_id = Column(BigInteger, nullable=False)
    is_approved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    required_gender = Column(String(50), nullable=True)  

    creator = relationship(
        "User",
        foreign_keys=[creator_id],
        primaryjoin="User.telegram_id==Meeting.creator_id",
        viewonly=True
    )
    participants = relationship("MeetingParticipant", back_populates="meeting", cascade="all, delete-orphan")


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"
    id = Column(BigInteger, primary_key=True, index=True)
    meeting_id = Column(BigInteger, ForeignKey("meetings.id"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False, index=True)
    joined_at = Column(DateTime, default=func.now())

    __table_args__ = (
        UniqueConstraint('meeting_id', 'user_id', name='_meeting_user_uc'),
    )

    meeting = relationship("Meeting", back_populates="participants")
    user = relationship("User", back_populates="participations")
