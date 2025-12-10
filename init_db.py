from db import async_engine
from sqlalchemy import text

async def create_indexes():
    async with async_engine.begin() as conn:
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_meetings_category_datetime 
            ON meetings (category, date_time);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_meetings_required_gender 
            ON meetings (required_gender);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_meetings_creator_datetime 
            ON meetings (creator_id, date_time DESC);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_meetings_lat_lon 
            ON meetings (latitude, longitude);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_meetings_search_ai 
            ON meetings (date_time, category, required_gender);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_participants_user_meeting 
            ON meeting_participants (user_id, meeting_id);
        """))
    print("✅ Все индексы созданы")
