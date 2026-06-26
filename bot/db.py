"""
Пул соединений с БД + хелперы.
Поддержка SQLite (локальная разработка) и PostgreSQL (production).
"""
import json
import logging
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import asyncpg
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)

# Переключатель: SQLite или PostgreSQL
USE_SQLITE = os.getenv("USE_SQLITE", "true").lower() == "true"
DB_PATH = Path(__file__).parent / "benzin.db"
DATABASE_URL = os.getenv("DATABASE_URL", "")

_db: Any = None


# === Инициализация ===
async def init_db():
    """Инициализирует БД."""
    global _db
    if USE_SQLITE:
        _db = await aiosqlite.connect(str(DB_PATH))
        _db.row_factory = aiosqlite.Row
        # PRAGMA оптимизации для скорости
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        await _db.execute("PRAGMA busy_timeout=5000")  # 5 сек max wait на блокировку
        await _db.execute("PRAGMA cache_size=-20000")  # 20MB кеш
        await _db.execute("PRAGMA temp_store=MEMORY")  # temp таблицы в RAM
        await _db.execute("PRAGMA synchronous=NORMAL")  # чуть быстрее WAL
        await _create_schema_sqlite(_db)
        await _db.commit()
    else:
        _db = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=4,         # больше соединений
            max_size=20,
            command_timeout=30, # быстрее fail при проблемах
            ssl="require",
            # Supabase free tier использует pgbouncer в Transaction mode
            # который не поддерживает named prepared statements.
            # statement_cache_size=0 отключает кэш → безопасно для pgbouncer.
            statement_cache_size=0,
        )
        await _create_schema_pg(_db)


async def close_db():
    """Закрывает БД."""
    global _db
    if _db:
        await _db.close()
        _db = None


# === Создание схемы ===
async def _create_schema_sqlite(db):
    """Создаёт схему в SQLite (CREATE IF NOT EXISTS) + миграции."""
    schema_path = Path(__file__).parent.parent / "db" / "schema_sqlite.sql"
    if not schema_path.exists():
        return

    # Сначала добавляем недостающие колонки в существующие таблицы
    await _migrate_sqlite(db)

    # Потом выполняем schema (CREATE IF NOT EXISTS пропустит существующие)
    sql = schema_path.read_text(encoding="utf-8")
    await db.executescript(sql)

    # Создаём индексы, которые зависят от миграций
    await _create_indexes_sqlite(db)
    await db.commit()


async def _migrate_sqlite(db):
    """Добавляет недостающие колонки в существующие таблицы (для уже созданных БД)."""
    async with db.execute("PRAGMA table_info(subscriptions)") as cur:
        cols = {row[1] for row in await cur.fetchall()}

    if "center_lat" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN center_lat REAL")
    if "center_lon" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN center_lon REAL")

    # Создаём owner_stations если её нет
    await db.execute(
        """CREATE TABLE IF NOT EXISTS owner_stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
            inn TEXT,
            role TEXT DEFAULT 'owner',
            is_verified INTEGER DEFAULT 0,
            moderator_id INTEGER REFERENCES users(id),
            rejection_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            verified_at TEXT,
            UNIQUE(user_id, station_id)
        )"""
    )

    # Добавляем UNIQUE на subscriptions (если ещё нет) — защита от дублей
    try:
        # Сначала удаляем дубли (если есть)
        await db.execute(
            """DELETE FROM subscriptions
               WHERE id NOT IN (
                   SELECT MIN(id) FROM subscriptions
                   WHERE user_id IS NOT NULL AND station_id IS NOT NULL
                   GROUP BY user_id, station_id
               )
               AND station_id IS NOT NULL"""
        )
        # Создаём UNIQUE index (в SQLite это и есть constraint)
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_unique "
            "ON subscriptions (user_id, station_id) WHERE station_id IS NOT NULL"
        )
    except Exception as e:
        logger.warning(f"Could not add UNIQUE to subscriptions: {e}")


async def _create_indexes_sqlite(db):
    """Создаёт индексы (можно безопасно вызывать повторно)."""
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_geo "
        "ON subscriptions (center_lat, center_lon)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_owner_stations_user "
        "ON owner_stations (user_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_owner_stations_station "
        "ON owner_stations (station_id)"
    )
    # Составной индекс для get_station_current_status (фильтр по station + время)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_reports_station_created "
        "ON reports (station_id, created_at DESC)"
    )
    # Индекс для get_recent_fuel_reports (по времени)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_reports_created "
        "ON reports (created_at DESC)"
    )
    # Бейджи пользователей
    await db.execute(
        "CREATE TABLE IF NOT EXISTS user_badges ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER NOT NULL, "
        "badge_code TEXT NOT NULL, "
        "awarded_at TEXT DEFAULT (datetime('now')), "
        "UNIQUE(user_id, badge_code))"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_badges_user "
        "ON user_badges (user_id)"
    )
    # Premium-подписки (Telegram Stars)
    await db.execute(
        """CREATE TABLE IF NOT EXISTS premium_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_payment_charge_id TEXT,
            stars_amount INTEGER,
            started_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )"""
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_premium_user "
        "ON premium_subscriptions (user_id, is_active)"
    )


async def _create_schema_pg(pool):
    """Создаёт минимальные таблицы в PostgreSQL (если их ещё нет).

    В production (Supabase/managed PG) считаем что schema.sql уже применён
    вручную (через Dashboard или psql). Эта функция только добавляет недостающее.
    """
    async with pool.acquire() as conn:
        # user_badges
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS user_badges (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                badge_code TEXT NOT NULL,
                awarded_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id, badge_code)
            )"""
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_badges_user ON user_badges (user_id)"
        )
        # premium_subscriptions
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS premium_subscriptions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                telegram_payment_charge_id TEXT,
                stars_amount INTEGER,
                started_at TIMESTAMPTZ DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                is_active BOOLEAN DEFAULT TRUE
            )"""
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_premium_user ON premium_subscriptions (user_id) WHERE is_active"
        )


from contextlib import asynccontextmanager

@asynccontextmanager
async def get_connection():
    """Async context manager: yield connection (aiosqlite или asyncpg)."""
    if USE_SQLITE:
        yield _db
    else:
        async with _db.acquire() as conn:
            yield conn


# === Универсальные хелперы ===
async def _fetch(sql: str, *args, one: bool = False):
    """Универсальный fetch. Возвращает dict (SQLite) или list[dict] (PostgreSQL)."""
    if USE_SQLITE:
        async with _db.execute(sql, args) as cur:
            if one:
                row = await cur.fetchone()
                return dict(row) if row else None
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    async with _db.acquire() as conn:
        if one:
            row = await conn.fetchrow(sql, *args)
            return dict(row) if row else None
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


# === Бейджи пользователей ===
BADGE_CATALOG = {
    "newcomer": {"name": "Новичок", "emoji": "🥉", "desc": "Первый отчёт"},
    "active": {"name": "Активный", "emoji": "🥈", "desc": "10+ отчётов"},
    "expert": {"name": "Эксперт", "emoji": "🥇", "desc": "100+ отчётов"},
    "top_region": {"name": "Топ региона", "emoji": "👑", "desc": "Самый активный в своём городе"},
    "pioneer": {"name": "Первопроходец", "emoji": "🔍", "desc": "Первый отчёт о новой АЗС"},
    "verified_owner": {"name": "Verified", "emoji": "✅", "desc": "Подтверждённый владелец АЗС"},
}


async def award_badge(user_id: int, badge_code: str) -> bool:
    """Выдаёт бейдж пользователю. Возвращает True если новый, False если уже был."""
    if badge_code not in BADGE_CATALOG:
        return False
    if USE_SQLITE:
        try:
            async with _db.execute(
                "INSERT INTO user_badges (user_id, badge_code) VALUES (?, ?)",
                (user_id, badge_code),
            ) as cur:
                await cur.fetchone()
            await _db.commit()
            return True
        except Exception:
            await _db.rollback()
            return False  # уже есть (UNIQUE constraint)
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO user_badges (user_id, badge_code)
                   VALUES ($1, $2)
                   ON CONFLICT (user_id, badge_code) DO NOTHING
                   RETURNING id""",
                user_id, badge_code,
            )
            return row is not None


async def get_user_badges(user_id: int) -> list:
    """Возвращает список бейджей пользователя с метаданными."""
    if USE_SQLITE:
        async with _db.execute(
            "SELECT badge_code, awarded_at FROM user_badges "
            "WHERE user_id = ? ORDER BY awarded_at",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {**BADGE_CATALOG.get(r["badge_code"], {"name": r["badge_code"], "emoji": "🏅", "desc": ""}),
             "code": r["badge_code"],
             "awarded_at": r["awarded_at"]}
            for r in rows
        ]
    async with _db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT badge_code, awarded_at FROM user_badges "
            "WHERE user_id = $1 ORDER BY awarded_at",
            user_id,
        )
    return [
        {**BADGE_CATALOG.get(r["badge_code"], {"name": r["badge_code"], "emoji": "🏅", "desc": ""}),
         "code": r["badge_code"],
         "awarded_at": r["awarded_at"].isoformat() if r["awarded_at"] else None}
        for r in rows
    ]


async def check_and_award_badges(user_id: int) -> list:
    """Проверяет и выдаёт бейджи по текущей статистике. Возвращает список новых бейджей."""
    if USE_SQLITE:
        # total_reports
        async with _db.execute(
            "SELECT total_reports FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return []
        total = row["total_reports"] or 0
        # is_owner + verified
        async with _db.execute(
            "SELECT COUNT(*) as c FROM owner_stations "
            "WHERE user_id = ? AND is_verified = 1",
            (user_id,),
        ) as cur:
            v = await cur.fetchone()
        has_verified_station = (v["c"] or 0) > 0
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT total_reports FROM users WHERE id = $1", user_id
            )
            if not row:
                return []
            total = row["total_reports"] or 0
            v = await conn.fetchrow(
                "SELECT COUNT(*) as c FROM owner_stations "
                "WHERE user_id = $1 AND is_verified = TRUE",
                user_id,
            )
            has_verified_station = (v["c"] or 0) > 0

    new_badges = []
    if total >= 1:
        if await award_badge(user_id, "newcomer"):
            new_badges.append("newcomer")
    if total >= 10:
        if await award_badge(user_id, "active"):
            new_badges.append("active")
    if total >= 100:
        if await award_badge(user_id, "expert"):
            new_badges.append("expert")
    if has_verified_station:
        if await award_badge(user_id, "verified_owner"):
            new_badges.append("verified_owner")

    return new_badges


async def get_user_stats_summary(user_id: int) -> dict:
    """Возвращает репутацию, отчёты и список бейджей для /profile."""
    if USE_SQLITE:
        async with _db.execute(
            "SELECT reputation, total_reports, confirmed_reports, region, city "
            "FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return {}
        stats = dict(row)
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT reputation, total_reports, confirmed_reports, region, city "
                "FROM users WHERE id = $1",
                user_id,
            )
            if not row:
                return {}
            stats = dict(row)
    stats["badges"] = await get_user_badges(user_id)
    return stats


# === Premium-подписки (Telegram Stars) ===
async def activate_premium(user_id: int, days: int = 30, charge_id: str = "", stars: int = 0) -> dict:
    """Активирует premium на N дней. Возвращает {expires_at}."""
    from datetime import datetime, timedelta
    if USE_SQLITE:
        expires = (datetime.now() + timedelta(days=days)).isoformat()
        await _db.execute(
            "UPDATE premium_subscriptions SET is_active = 0 WHERE user_id = ?",
            (user_id,),
        )
        async with _db.execute(
            """INSERT INTO premium_subscriptions
               (user_id, telegram_payment_charge_id, stars_amount, expires_at, is_active)
               VALUES (?, ?, ?, ?, 1)""",
            (user_id, charge_id, stars, expires),
        ) as cur:
            sub_id = cur.lastrowid
        await _db.commit()
        return {"id": sub_id, "expires_at": expires}
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE premium_subscriptions SET is_active = FALSE WHERE user_id = $1",
                user_id,
            )
            row = await conn.fetchrow(
                """INSERT INTO premium_subscriptions
                   (user_id, telegram_payment_charge_id, stars_amount, expires_at, is_active)
                   VALUES ($1, $2, $3, NOW() + ($4 || ' days')::interval, TRUE)
                   RETURNING id, expires_at""",
                user_id, charge_id, stars, str(days),
            )
            return {"id": row["id"], "expires_at": row["expires_at"].isoformat()}


async def is_premium(user_id: int) -> bool:
    """Проверяет, активна ли premium-подписка."""
    if USE_SQLITE:
        async with _db.execute(
            """SELECT expires_at FROM premium_subscriptions
               WHERE user_id = ? AND is_active = 1
               ORDER BY expires_at DESC LIMIT 1""",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return False
        try:
            exp = datetime.fromisoformat(row["expires_at"])
            return exp > datetime.now()
        except (ValueError, TypeError):
            return False
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT expires_at FROM premium_subscriptions
                   WHERE user_id = $1 AND is_active = TRUE
                   ORDER BY expires_at DESC LIMIT 1""",
                user_id,
            )
        if not row:
            return False
        return row["expires_at"] > datetime.now()


async def get_premium_info(user_id: int) -> dict | None:
    """Возвращает инфо о premium-подписке или None."""
    if USE_SQLITE:
        async with _db.execute(
            """SELECT started_at, expires_at, stars_amount, telegram_payment_charge_id
               FROM premium_subscriptions
               WHERE user_id = ? AND is_active = 1
               ORDER BY expires_at DESC LIMIT 1""",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return dict(row)
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT started_at, expires_at, stars_amount, telegram_payment_charge_id
                   FROM premium_subscriptions
                   WHERE user_id = $1 AND is_active = TRUE
                   ORDER BY expires_at DESC LIMIT 1""",
                user_id,
            )
        return dict(row) if row else None
    async with _db.acquire() as conn:
        if one:
            row = await conn.fetchrow(sql, *args)
            return dict(row) if row else None
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def _execute(sql: str, *args, returning: bool = False):
    """Универсальный execute.

    При returning=True: для SQLite возвращает cursor.lastrowid, для PG — результат RETURNING.
    """
    if USE_SQLITE:
        async with _db.execute(sql, args) as cur:
            await _db.commit()
            if returning:
                return cur.lastrowid
        return None
    async with _db.acquire() as conn:
        if returning:
            row = await conn.fetchrow(sql, *args)
            return row[0] if row else None
        await conn.execute(sql, *args)


# === Пользователи ===
async def upsert_user(
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    language_code: str | None = None,
) -> int:
    """Создаёт или обновляет пользователя. Возвращает его id."""
    if USE_SQLITE:
        # Сначала проверяем, есть ли уже
        async with _db.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            user_id = row[0]
            await _db.execute(
                """UPDATE users SET username=?, first_name=?, last_name=?, language_code=?, last_active_at=datetime('now')
                   WHERE id=?""",
                (username, first_name, last_name, language_code, user_id),
            )
            await _db.commit()
            return user_id
        # Создаём нового
        async with _db.execute(
            """INSERT INTO users (telegram_id, username, first_name, last_name, language_code, last_active_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (telegram_id, username, first_name, last_name, language_code),
        ) as cur:
            user_id = cur.lastrowid
        await _db.commit()
        return user_id
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM users WHERE telegram_id = $1", telegram_id
            )
            if row:
                await conn.execute(
                    """UPDATE users SET username=$1, first_name=$2, last_name=$3, language_code=$4, last_active_at=NOW()
                       WHERE id=$5""",
                    username, first_name, last_name, language_code, row["id"],
                )
                return row["id"]
            new_row = await conn.fetchrow(
                """INSERT INTO users (telegram_id, username, first_name, last_name, language_code, last_active_at)
                   VALUES ($1, $2, $3, $4, $5, NOW()) RETURNING id""",
                telegram_id, username, first_name, last_name, language_code,
            )
            return new_row["id"]


async def mark_user_blocked(telegram_id: int) -> None:
    """Помечает пользователя заблокированным (если он заблокировал бота)."""
    if USE_SQLITE:
        await _db.execute(
            "UPDATE users SET is_blocked = 1 WHERE telegram_id = ?",
            (telegram_id,),
        )
        await _db.commit()
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_blocked = TRUE WHERE telegram_id = $1",
                telegram_id,
            )


async def get_or_create_user(message) -> int:
    """Создаёт/обновляет пользователя из сообщения Telegram."""
    user = message.from_user
    return await upsert_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
    )


# === АЗС и поиск ===
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между точками в км."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.asin(math.sqrt(a))
    return R * c


async def find_nearest_stations(
    lat: float, lon: float,
    fuel_type: str | None = None,
    limit: int = 5, radius_km: int = 50,
) -> list:
    """Ищет ближайшие АЗС к точке (в SQLite — простой фильтр по bbox + haversine)."""
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / (111.0 * math.cos(math.radians(lat)))

    if USE_SQLITE:
        # SQLite: грубый bbox фильтр, потом haversine в Python
        if fuel_type:
            sql = """
                SELECT id, name, operator, city, address, lat, lon, fuel_types, is_verified
                FROM stations
                WHERE is_active = 1
                  AND lat BETWEEN ? AND ?
                  AND lon BETWEEN ? AND ?
                  AND fuel_types LIKE ?
            """
            params = (lat - lat_delta, lat + lat_delta,
                      lon - lon_delta, lon + lon_delta, f'%"{fuel_type}"%')
        else:
            sql = """
                SELECT id, name, operator, city, address, lat, lon, fuel_types, is_verified
                FROM stations
                WHERE is_active = 1
                  AND lat BETWEEN ? AND ?
                  AND lon BETWEEN ? AND ?
            """
            params = (lat - lat_delta, lat + lat_delta,
                      lon - lon_delta, lon + lon_delta)

        async with _db.execute(sql, params) as cur:
            rows = await cur.fetchall()

        # Haversine
        results = []
        for row in rows:
            d = dict(row)
            dist = _haversine_km(lat, lon, d["lat"], d["lon"])
            if dist <= radius_km:
                d["distance_km"] = dist
                results.append(d)
        results.sort(key=lambda x: x["distance_km"])
        return results[:limit]
    else:
        # PostgreSQL: точный запрос с haversine в SQL
        if fuel_type:
            sql = """
                WITH nearest AS (
                    SELECT
                        id, name, operator, city, address, lat, lon, fuel_types, is_verified,
                        (
                            6371 * acos(
                                cos(radians($1)) * cos(radians(lat)) *
                                cos(radians(lon) - radians($2)) +
                                sin(radians($1)) * sin(radians(lat))
                            )
                        ) AS distance_km
                    FROM stations
                    WHERE is_active = TRUE
                      AND lat BETWEEN $1 - $4 AND $1 + $4
                      AND lon BETWEEN $2 - $5 AND $2 + $5
                      AND $3 = ANY(fuel_types)
                )
                SELECT *
                FROM nearest
                WHERE distance_km <= $6
                ORDER BY distance_km ASC
                LIMIT $7
            """
        else:
            sql = """
                WITH nearest AS (
                    SELECT
                        id, name, operator, city, address, lat, lon, fuel_types, is_verified,
                        (
                            6371 * acos(
                                cos(radians($1)) * cos(radians(lat)) *
                                cos(radians(lon) - radians($2)) +
                                sin(radians($1)) * sin(radians(lat))
                            )
                        ) AS distance_km
                    FROM stations
                    WHERE is_active = TRUE
                      AND lat BETWEEN $1 - $3 AND $1 + $3
                      AND lon BETWEEN $2 - $4 AND $2 + $4
                )
                SELECT *
                FROM nearest
                WHERE distance_km <= $5
                ORDER BY distance_km ASC
                LIMIT $6
            """
        async with _db.acquire() as conn:
            if fuel_type:
                rows = await conn.fetch(
                    sql, lat, lon, fuel_type, lat_delta, lon_delta, radius_km, limit
                )
            else:
                rows = await conn.fetch(
                    sql, lat, lon, lat_delta, lon_delta, radius_km, limit
                )
        return [dict(r) for r in rows]


async def get_station_by_id(station_id: int) -> dict | None:
    """Получает АЗС по id."""
    if USE_SQLITE:
        async with _db.execute(
            "SELECT * FROM stations WHERE id = ?", (station_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM stations WHERE id = $1", station_id)
        return dict(row) if row else None


async def update_station_address(station_id: int, address: str, city: str, region: str) -> None:
    """Обновляет адрес, город и регион АЗС (используется при обогащении через reverse geocoding)."""
    if USE_SQLITE:
        await _db.execute(
            """UPDATE stations
               SET address = COALESCE(NULLIF(?, ''), address),
                   city = COALESCE(NULLIF(?, ''), city),
                   region = COALESCE(NULLIF(?, ''), region),
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (address, city, region, station_id),
        )
        await _db.commit()
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                """UPDATE stations
                   SET address = COALESCE(NULLIF($1, ''), address),
                       city = COALESCE(NULLIF($2, ''), city),
                       region = COALESCE(NULLIF($3, ''), region),
                       updated_at = NOW()
                   WHERE id = $4""",
                address, city, region, station_id,
            )


async def get_stations_without_address(
    city: str | None = None, limit: int | None = None
) -> list:
    """Возвращает АЗС без адреса (для обогащения через reverse geocoding)."""
    if USE_SQLITE:
        sql = """SELECT id, name, lat, lon, address, city, region
                 FROM stations
                 WHERE is_active = 1
                   AND (address IS NULL OR address = '' OR city IS NULL OR city = '')"""
        params: list = []
        if city:
            sql += " AND (city LIKE ? OR name LIKE ?)"
            like = f"%{city}%"
            params.extend([like, like])
        sql += " ORDER BY id LIMIT ?"
        params.append(limit or 1000)
        async with _db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    async with _db.acquire() as conn:
        sql = """SELECT id, name, lat, lon, address, city, region
                 FROM stations
                 WHERE is_active = TRUE
                   AND (address IS NULL OR address = '' OR city IS NULL OR city = '')"""
        params = []
        if city:
            sql += " AND (city ILIKE $1 OR name ILIKE $1)"
            params.append(f"%{city}%")
        sql += f" ORDER BY id LIMIT {limit or 1000}"
        rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]


async def get_user_id_by_telegram_id(telegram_id: int) -> int | None:
    """Возвращает внутренний id пользователя по telegram_id."""
    if USE_SQLITE:
        async with _db.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM users WHERE telegram_id = $1", telegram_id
            )
        return row["id"] if row else None


async def add_report(
    station_id: int,
    fuel_type: str,
    available: bool | None,
    user_id: int | None = None,
    price: float | None = None,
    queue_size: int | None = None,
    has_limit: bool = False,
    limit_liters: int | None = None,
    comment: str | None = None,
    source: str = "user",
) -> int:
    """Добавляет отчёт о наличии топлива.

    available: True / False / None (None = "кончается").
    В SQLite available NOT NULL, поэтому None хранится как 2.
    Также инкрементит users.total_reports и last_active_at.
    """
    expires_at_dt = datetime.now() + timedelta(hours=2)
    if USE_SQLITE:
        expires_at = expires_at_dt.isoformat()
    else:
        expires_at = expires_at_dt  # asyncpg требует datetime, не строку

    if USE_SQLITE:
        # SQLite: True=1, False=0, None=2 ("кончается")
        if available is True:
            avail_int = 1
        elif available is False:
            avail_int = 0
        else:
            avail_int = 2
        has_limit_int = 1 if has_limit else 0

        async with _db.execute(
            """INSERT INTO reports (
                station_id, user_id, fuel_type, available, price,
                queue_size, has_limit, limit_liters, comment, source, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (station_id, user_id, fuel_type, avail_int, price,
             queue_size, has_limit_int, limit_liters, comment, source, expires_at),
        ) as cur:
            report_id = cur.lastrowid
        if user_id:
            await _db.execute(
                "UPDATE users SET total_reports = total_reports + 1, last_active_at = datetime('now') WHERE id = ?",
                (user_id,),
            )
        await _db.commit()
        return report_id
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO reports (
                    station_id, user_id, fuel_type, available, price,
                    queue_size, has_limit, limit_liters, comment, source, expires_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING id
                """,
                station_id, user_id, fuel_type, available, price,
                queue_size, has_limit, limit_liters, comment, source, expires_at,
            )
            if user_id:
                await conn.execute(
                    "UPDATE users SET total_reports = total_reports + 1, last_active_at = NOW() WHERE id = $1",
                    user_id,
                )
            return row["id"]


async def add_subscription(
    user_id: int,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: int = 5,
    fuel_type: str | None = None,
    station_id: int | None = None,
) -> int:
    """Создаёт подписку: либо гео (lat/lon), либо на конкретную АЗС (station_id)."""
    fuel = fuel_type or "92"
    if USE_SQLITE:
        async with _db.execute(
            """INSERT INTO subscriptions
                (user_id, station_id, fuel_type, radius_km, center_lat, center_lon)
                VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, station_id, fuel, radius_km, lat, lon),
        ) as cur:
            sub_id = cur.lastrowid
        await _db.commit()
        return sub_id
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO subscriptions
                    (user_id, station_id, fuel_type, radius_km, center_lat, center_lon)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id""",
                user_id, station_id, fuel, radius_km, lat, lon,
            )
            return row["id"]


async def find_stations_by_name(query: str, limit: int = 5) -> list:
    """Ищет АЗС по имени (для /find без геолокации)."""
    if USE_SQLITE:
        sql = """
            SELECT id, name, operator, city, address, lat, lon, is_verified
            FROM stations
            WHERE is_active = 1
              AND (name LIKE ? OR operator LIKE ? OR city LIKE ?)
            ORDER BY
                CASE WHEN name LIKE ? THEN 0 ELSE 1 END,
                operator,
                name
            LIMIT ?
        """
        like = f"%{query}%"
        async with _db.execute(sql, (like, like, like, like, limit)) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, operator, city, address, lat, lon, is_verified
                FROM stations
                WHERE is_active = TRUE
                  AND (name ILIKE $1 OR operator ILIKE $1 OR city ILIKE $1)
                ORDER BY
                    CASE WHEN name ILIKE $1 THEN 0 ELSE 1 END,
                    operator NULLS LAST,
                    name
                LIMIT $2
                """,
                f"%{query}%", limit,
            )
        return [dict(r) for r in rows]


async def get_station_current_status(station_id: int) -> list:
    """Возвращает текущий статус АЗС по всем видам топлива (свежие < 24ч).

    available: True / False / None ("кончается")
    """
    if USE_SQLITE:
        async with _db.execute(
            """SELECT fuel_type, available, price, queue_size, has_limit, limit_liters, confidence, created_at
               FROM reports
               WHERE station_id = ? AND created_at > datetime('now', '-1 day')
               ORDER BY fuel_type, confidence DESC, created_at DESC""",
            (station_id,)
        ) as cur:
            rows = await cur.fetchall()
        # Группируем по fuel_type (берём последний с максимальным confidence)
        # Конвертируем SQLite 2 → None ("кончается")
        seen = set()
        result = []
        for row in rows:
            r = dict(row)
            if r["fuel_type"] in seen:
                continue
            seen.add(r["fuel_type"])
            if r.get("available") == 1:
                r["available"] = True
            elif r.get("available") == 0:
                r["available"] = False
            elif r.get("available") == 2:
                r["available"] = None
            result.append(r)
        return result
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (fuel_type)
                    fuel_type, available, price, queue_size, has_limit,
                    limit_liters, confidence, created_at AS last_report_at
                FROM reports
                WHERE station_id = $1
                  AND created_at > NOW() - INTERVAL '24 hours'
                ORDER BY fuel_type, confidence DESC, created_at DESC
                """,
                station_id,
            )
        return [dict(r) for r in rows]


async def get_stations_with_statuses(stations: list) -> list:
    """Bulk-получение статусов для списка АЗС одним запросом (избегаем N+1).

    Возвращает тот же список stations, но с добавленным полем 'statuses' и 'has_data'.
    Использует ROW_NUMBER() window function в SQLite и DISTINCT ON в PG.
    """
    if not stations:
        return stations

    station_ids = [s["id"] for s in stations]
    placeholders = ",".join("?" for _ in station_ids)

    if USE_SQLITE:
        # Один запрос с window function — для каждой АЗС и fuel_type берём последний
        async with _db.execute(
            f"""SELECT station_id, fuel_type, available, price, queue_size,
                       has_limit, limit_liters, confidence, created_at
                FROM (
                    SELECT station_id, fuel_type, available, price, queue_size,
                           has_limit, limit_liters, confidence, created_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY station_id, fuel_type
                               ORDER BY confidence DESC, created_at DESC
                           ) AS rn
                    FROM reports
                    WHERE station_id IN ({placeholders})
                      AND created_at > datetime('now', '-1 day')
                )
                WHERE rn = 1""",
            station_ids,
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT DISTINCT ON (station_id, fuel_type)
                        station_id, fuel_type, available, price, queue_size,
                        has_limit, limit_liters, confidence,
                        created_at AS last_report_at
                    FROM reports
                    WHERE station_id = ANY($1)
                      AND created_at > NOW() - INTERVAL '24 hours'
                    ORDER BY station_id, fuel_type, confidence DESC, created_at DESC""",
                station_ids,
            )

    # Группируем по station_id
    by_station: dict[int, list] = {}
    for r in rows:
        d = dict(r) if not isinstance(r, dict) else r
        if USE_SQLITE:
            # Конвертируем int → bool/None для SQLite
            if d.get("available") == 1:
                d["available"] = True
            elif d.get("available") == 0:
                d["available"] = False
            elif d.get("available") == 2:
                d["available"] = None
        sid = d["station_id"]
        by_station.setdefault(sid, []).append(d)

    # Прикрепляем статусы к станциям
    for s in stations:
        sid = s["id"]
        statuses = by_station.get(sid, [])
        s["statuses"] = statuses
        s["has_data"] = len(statuses) > 0

    return stations


# === Аналитика ===
async def log_event(user_id: int | None, event_type: str, payload: dict | None = None):
    """Логирует событие. user_id — это internal id из users.id. Если None, не пишет user_id."""
    if USE_SQLITE:
        await _db.execute(
            "INSERT INTO events (user_id, event_type, payload) VALUES (?, ?, ?)",
            (user_id, event_type, json.dumps(payload or {})),
        )
        await _db.commit()
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "INSERT INTO events (user_id, event_type, payload) VALUES ($1, $2, $3::jsonb)",
                user_id, event_type, json.dumps(payload or {}),
            )


# === Приоритизация источников ===
# Чем выше priority, тем больше доверия к источнику.
# При конфликте цен берётся источник с max(priority × confidence).
SOURCE_PRIORITY = {
    "user":      1.00,  # отчёт водителя на АЗС — самый доверенный
    "owner":     1.00,  # владелец АЗС
    "telegram":  0.85,  # Telegram-каналы с ценами (бензин_price и т.д.)
    "yandex":    0.80,  # Яндекс.Заправки (официальный API)
    "lukoil":    0.75,  # сайт сети (точные цены своей сети)
    "gazprom":   0.75,
    "rosneft":   0.75,
    "tatneft":   0.75,
    "bashneft":  0.75,
    "2gis":      0.65,  # 2ГИС (если платный)
    "osm":       0.30,  # OSM (нет цен, только мета)
    "default":   0.50,
}


def get_source_priority(source: str) -> float:
    return SOURCE_PRIORITY.get(source, SOURCE_PRIORITY["default"])


# === Confidence модель ===
# Чем больше подтверждений и свежее данные — тем выше уверенность.
def calculate_confidence(
    source: str,
    age_hours: float,
    agreement_count: int = 1,
    base_confidence: float = 0.7,
) -> float:
    """Рассчитывает confidence (0..1) для отчёта.

    source: источник данных
    age_hours: сколько часов назад
    agreement_count: сколько других источников согласны с этой ценой
    base_confidence: базовая уверенность источника
    """
    # Свежесть: экспоненциальный спад
    freshness = max(0.1, 1.0 - (age_hours / 24.0) ** 0.5)
    # Согласие: +0.2 за каждый согласный источник
    agreement = min(0.4, agreement_count * 0.2)
    # Базовый confidence от источника
    base = base_confidence * get_source_priority(source)
    return min(1.0, base * freshness + agreement)


async def get_station_analytics(station_id: int, days: int = 30) -> dict:
    """Аналитика для владельца АЗС: просмотры, отчёты, подписчики, цены."""
    result = {
        "station_id": station_id,
        "period_days": days,
        "views": 0,
        "reports_30d": 0,
        "reports_by_fuel": {},
        "subscribers": 0,
        "avg_price": None,
        "last_price": None,
        "last_report_at": None,
        "views_chart": [],  # [{date, count}]
    }
    if USE_SQLITE:
        # Просмотры
        async with _db.execute(
            """SELECT DATE(created_at) as d, COUNT(*) as c FROM events
               WHERE event_type = 'station_viewed'
                 AND json_extract(payload, '$.station_id') = ?
                 AND created_at > datetime('now', ?)
               GROUP BY d ORDER BY d""",
            (station_id, f"-{days} days"),
        ) as cur:
            for r in await cur.fetchall():
                result["views_chart"].append({"date": r["d"], "count": r["c"]})
            result["views"] = sum(v["count"] for v in result["views_chart"])
        # Отчёты
        async with _db.execute(
            """SELECT fuel_type, COUNT(*) as c, AVG(price) as avg_p, MAX(price) as max_p, MIN(price) as min_p
               FROM reports
               WHERE station_id = ? AND created_at > datetime('now', ?)
               GROUP BY fuel_type""",
            (station_id, f"-{days} days"),
        ) as cur:
            total_avg = []
            for r in await cur.fetchall():
                result["reports_by_fuel"][r["fuel_type"]] = {
                    "count": r["c"],
                    "avg_price": float(r["avg_p"]) if r["avg_p"] else None,
                }
                if r["avg_p"]:
                    total_avg.append(float(r["avg_p"]))
            result["reports_30d"] = sum(v["count"] for v in result["reports_by_fuel"].values())
            result["avg_price"] = sum(total_avg) / len(total_avg) if total_avg else None
        # Подписчики
        async with _db.execute(
            "SELECT COUNT(*) as c FROM subscriptions WHERE station_id = ? AND is_active = 1",
            (station_id,),
        ) as cur:
            r = await cur.fetchone()
            result["subscribers"] = r["c"] if r else 0
        # Последний отчёт
        async with _db.execute(
            """SELECT fuel_type, available, price, created_at FROM reports
               WHERE station_id = ? ORDER BY created_at DESC LIMIT 1""",
            (station_id,),
        ) as cur:
            last = await cur.fetchone()
        if last:
            result["last_report_at"] = last["created_at"]
            result["last_price"] = float(last["price"]) if last["price"] else None
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT DATE(created_at) as d, COUNT(*) as c FROM events
                   WHERE event_type = 'station_viewed'
                     AND (payload->>'station_id')::int = $1
                     AND created_at > NOW() - ($2 || ' days')::interval
                   GROUP BY d ORDER BY d""",
                station_id, str(days),
            )
            for r in rows:
                result["views_chart"].append({"date": r["d"].isoformat(), "count": r["c"]})
            result["views"] = sum(v["count"] for v in result["views_chart"])
            rows = await conn.fetch(
                """SELECT fuel_type, COUNT(*) as c, AVG(price) as avg_p
                   FROM reports
                   WHERE station_id = $1 AND created_at > NOW() - ($2 || ' days')::interval
                   GROUP BY fuel_type""",
                station_id, str(days),
            )
            total_avg = []
            for r in rows:
                result["reports_by_fuel"][r["fuel_type"]] = {
                    "count": r["c"],
                    "avg_price": float(r["avg_p"]) if r["avg_p"] else None,
                }
                if r["avg_p"]:
                    total_avg.append(float(r["avg_p"]))
            result["reports_30d"] = sum(v["count"] for v in result["reports_by_fuel"].values())
            result["avg_price"] = sum(total_avg) / len(total_avg) if total_avg else None
            row = await conn.fetchrow(
                "SELECT COUNT(*) as c FROM subscriptions WHERE station_id = $1 AND is_active = TRUE",
                station_id,
            )
            result["subscribers"] = row["c"] if row else 0
            row = await conn.fetchrow(
                """SELECT fuel_type, available, price, created_at FROM reports
                   WHERE station_id = $1 ORDER BY created_at DESC LIMIT 1""",
                station_id,
            )
            if row:
                result["last_report_at"] = row["created_at"].isoformat()
                result["last_price"] = float(row["price"]) if row["price"] else None
    return result


async def get_best_price_for_station(
    station_id: int, fuel_type: str
) -> dict | None:
    """Возвращает лучшую цену для (station, fuel) по приоритету × свежести.

    Учитывает все источники, отдаёт отчёт с максимальным weighted_score.
    """
    if db.USE_SQLITE:
        cur = await _db.execute(
            """SELECT id, fuel_type, available, price, source, confidence, created_at
               FROM reports
               WHERE station_id = ? AND fuel_type = ? AND price IS NOT NULL
                 AND created_at > datetime('now', '-7 days')
               ORDER BY created_at DESC LIMIT 20""",
            (station_id, fuel_type),
        )
        rows = await cur.fetchall()
        rows = [dict(r) for r in rows]
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, fuel_type, available, price, source, confidence, created_at
                   FROM reports
                   WHERE station_id = $1 AND fuel_type = $2 AND price IS NOT NULL
                     AND created_at > NOW() - INTERVAL '7 days'
                   ORDER BY created_at DESC LIMIT 20""",
                station_id, fuel_type,
            )
            rows = [dict(r) for r in rows]

    if not rows:
        return None

    # Для каждого отчёта считаем score
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    scored = []
    for r in rows:
        created = r["created_at"]
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_h = (now - created).total_seconds() / 3600.0
        source = r.get("source") or "default"
        # Считаем сколько других отчётов согласны (в пределах ±2₽)
        agreement = sum(
            1 for other in rows
            if other["id"] != r["id"]
            and other.get("price") is not None
            and abs(float(other["price"]) - float(r["price"])) <= 2.0
        )
        score = calculate_confidence(source, age_h, agreement)
        r["weighted_score"] = score
        scored.append(r)

    # Лучший по score
    scored.sort(key=lambda x: x["weighted_score"], reverse=True)
    return scored[0]


async def get_all_prices_for_station(station_id: int) -> dict:
    """Возвращает все цены по всем источникам для станции.

    Формат: {fuel_type: [{source, price, age_hours, confidence, weighted_score, is_best}]}
    """
    if db.USE_SQLITE:
        cur = await _db.execute(
            """SELECT id, fuel_type, available, price, source, confidence, created_at
               FROM reports
               WHERE station_id = ? AND price IS NOT NULL
                 AND created_at > datetime('now', '-7 days')
               ORDER BY fuel_type, created_at DESC""",
            (station_id,),
        )
        rows = await cur.fetchall()
        rows = [dict(r) for r in rows]
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, fuel_type, available, price, source, confidence, created_at
                   FROM reports
                   WHERE station_id = $1 AND price IS NOT NULL
                     AND created_at > NOW() - INTERVAL '7 days'
                   ORDER BY fuel_type, created_at DESC""",
                station_id,
            )
            rows = [dict(r) for r in rows]

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    by_fuel: dict[str, list] = {}
    for r in rows:
        created = r["created_at"]
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_h = (now - created).total_seconds() / 3600.0
        r["age_hours"] = round(age_h, 1)
        r["price"] = float(r["price"]) if r["price"] else None
        r["confidence"] = float(r["confidence"]) if r.get("confidence") else 0.5
        source = r.get("source") or "default"
        r["source_priority"] = get_source_priority(source)
        # Считаем agreement
        fuel = r["fuel_type"]
        others = [x for x in rows if x["fuel_type"] == fuel and x["id"] != r["id"] and x["price"]]
        r["agreement"] = sum(1 for x in others if abs(x["price"] - r["price"]) <= 2.0)
        r["weighted_score"] = round(
            calculate_confidence(source, age_h, r["agreement"]), 3
        )
        # Конвертируем datetime в ISO для JSON
        r["created_at"] = created.isoformat()
        by_fuel.setdefault(fuel, []).append(r)

    # Помечаем лучший
    for fuel, items in by_fuel.items():
        if items:
            items.sort(key=lambda x: x["weighted_score"], reverse=True)
            items[0]["is_best"] = True
            for it in items[1:]:
                it["is_best"] = False

    return by_fuel



    """Аналитика для владельца АЗС: просмотры, отчёты, подписчики, цены."""
    result = {
        "station_id": station_id,
        "period_days": days,
        "views": 0,
        "reports_30d": 0,
        "reports_by_fuel": {},
        "subscribers": 0,
        "avg_price": None,
        "last_price": None,
        "last_report_at": None,
        "views_chart": [],  # [{date, count}]
    }
    if USE_SQLITE:
        # Просмотры
        async with _db.execute(
            """SELECT DATE(created_at) as d, COUNT(*) as c FROM events
               WHERE event_type = 'station_viewed'
                 AND json_extract(payload, '$.station_id') = ?
                 AND created_at > datetime('now', ?)
               GROUP BY d ORDER BY d""",
            (station_id, f"-{days} days"),
        ) as cur:
            for r in await cur.fetchall():
                result["views_chart"].append({"date": r["d"], "count": r["c"]})
            result["views"] = sum(v["count"] for v in result["views_chart"])
        # Отчёты
        async with _db.execute(
            """SELECT fuel_type, COUNT(*) as c, AVG(price) as avg_p, MAX(price) as max_p, MIN(price) as min_p
               FROM reports
               WHERE station_id = ? AND created_at > datetime('now', ?)
               GROUP BY fuel_type""",
            (station_id, f"-{days} days"),
        ) as cur:
            total_avg = []
            for r in await cur.fetchall():
                result["reports_by_fuel"][r["fuel_type"]] = {
                    "count": r["c"],
                    "avg_price": float(r["avg_p"]) if r["avg_p"] else None,
                }
                if r["avg_p"]:
                    total_avg.append(float(r["avg_p"]))
            result["reports_30d"] = sum(v["count"] for v in result["reports_by_fuel"].values())
            result["avg_price"] = sum(total_avg) / len(total_avg) if total_avg else None
        # Подписчики
        async with _db.execute(
            "SELECT COUNT(*) as c FROM subscriptions WHERE station_id = ? AND is_active = 1",
            (station_id,),
        ) as cur:
            r = await cur.fetchone()
            result["subscribers"] = r["c"] if r else 0
        # Последний отчёт
        async with _db.execute(
            """SELECT fuel_type, available, price, created_at FROM reports
               WHERE station_id = ? ORDER BY created_at DESC LIMIT 1""",
            (station_id,),
        ) as cur:
            last = await cur.fetchone()
        if last:
            result["last_report_at"] = last["created_at"]
            result["last_price"] = float(last["price"]) if last["price"] else None
    else:
        async with _db.acquire() as conn:
            # Просмотры
            rows = await conn.fetch(
                """SELECT DATE(created_at) as d, COUNT(*) as c FROM events
                   WHERE event_type = 'station_viewed'
                     AND (payload->>'station_id')::int = $1
                     AND created_at > NOW() - ($2 || ' days')::interval
                   GROUP BY d ORDER BY d""",
                station_id, str(days),
            )
            for r in rows:
                result["views_chart"].append({"date": r["d"].isoformat(), "count": r["c"]})
            result["views"] = sum(v["count"] for v in result["views_chart"])
            # Отчёты
            rows = await conn.fetch(
                """SELECT fuel_type, COUNT(*) as c, AVG(price) as avg_p
                   FROM reports
                   WHERE station_id = $1 AND created_at > NOW() - ($2 || ' days')::interval
                   GROUP BY fuel_type""",
                station_id, str(days),
            )
            total_avg = []
            for r in rows:
                result["reports_by_fuel"][r["fuel_type"]] = {
                    "count": r["c"],
                    "avg_price": float(r["avg_p"]) if r["avg_p"] else None,
                }
                if r["avg_p"]:
                    total_avg.append(float(r["avg_p"]))
            result["reports_30d"] = sum(v["count"] for v in result["reports_by_fuel"].values())
            result["avg_price"] = sum(total_avg) / len(total_avg) if total_avg else None
            # Подписчики
            row = await conn.fetchrow(
                "SELECT COUNT(*) as c FROM subscriptions WHERE station_id = $1 AND is_active = TRUE",
                station_id,
            )
            result["subscribers"] = row["c"] if row else 0
            # Последний отчёт
            row = await conn.fetchrow(
                """SELECT fuel_type, available, price, created_at FROM reports
                   WHERE station_id = $1 ORDER BY created_at DESC LIMIT 1""",
                station_id,
            )
            if row:
                result["last_report_at"] = row["created_at"].isoformat()
                result["last_price"] = float(row["price"]) if row["price"] else None
    return result


async def get_stats() -> dict:
    """Глобальная статистика."""
    if USE_SQLITE:
        stats = {}
        async with _db.execute("SELECT COUNT(*) as c FROM stations WHERE is_active = 1") as cur:
            stats["stations_count"] = (await cur.fetchone())[0]
        async with _db.execute("SELECT COUNT(*) as c FROM users") as cur:
            stats["users_count"] = (await cur.fetchone())[0]
        async with _db.execute("SELECT COUNT(*) as c FROM reports WHERE created_at > datetime('now', '-1 day')") as cur:
            stats["reports_24h"] = (await cur.fetchone())[0]
        async with _db.execute("SELECT COUNT(DISTINCT city) as c FROM stations WHERE city IS NOT NULL") as cur:
            stats["cities_count"] = (await cur.fetchone())[0]
        return stats
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    (SELECT COUNT(*) FROM stations WHERE is_active) AS stations_count,
                    (SELECT COUNT(*) FROM users) AS users_count,
                    (SELECT COUNT(*) FROM reports WHERE created_at > NOW() - INTERVAL '24 hours') AS reports_24h,
                    (SELECT COUNT(DISTINCT city) FROM stations WHERE city IS NOT NULL) AS cities_count
            """)
        return dict(row)


# === Push-уведомления ===
async def get_recent_fuel_reports(minutes: int = 5) -> list:
    """Возвращает свежие отчёты о наличии топлива (за последние N минут).

    Каждый отчёт дополнен prev_available и prev_price — предыдущим состоянием
    той же АЗС+топлива (нужно для push-сценариев "появилось" и "цена упала").
    """
    if USE_SQLITE:
        async with _db.execute(
            """SELECT r.id, r.station_id, r.fuel_type, r.available, r.queue_size, r.price,
                      s.name, s.lat, s.lon, s.city, s.address,
                      (SELECT r2.available FROM reports r2
                         WHERE r2.station_id = r.station_id AND r2.fuel_type = r.fuel_type
                           AND r2.id < r.id ORDER BY r2.id DESC LIMIT 1) AS prev_available,
                      (SELECT r2.price FROM reports r2
                         WHERE r2.station_id = r.station_id AND r2.fuel_type = r.fuel_type
                           AND r2.id < r.id AND r2.price IS NOT NULL
                           ORDER BY r2.id DESC LIMIT 1) AS prev_price
               FROM reports r
               JOIN stations s ON s.id = r.station_id
               WHERE r.created_at > datetime('now', ?)
                 AND r.available IN (1, 2)
                 AND s.is_active = 1
               ORDER BY r.created_at DESC""",
            (f"-{minutes} minutes",),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT r.id, r.station_id, r.fuel_type, r.available, r.queue_size, r.price,
                         s.name, s.lat, s.lon, s.city, s.address,
                         (SELECT r2.available FROM reports r2
                            WHERE r2.station_id = r.station_id AND r2.fuel_type = r.fuel_type
                              AND r2.id < r.id ORDER BY r2.id DESC LIMIT 1) AS prev_available,
                         (SELECT r2.price FROM reports r2
                            WHERE r2.station_id = r.station_id AND r2.fuel_type = r.fuel_type
                              AND r2.id < r.id AND r2.price IS NOT NULL
                              ORDER BY r2.id DESC LIMIT 1) AS prev_price
                  FROM reports r
                  JOIN stations s ON s.id = r.station_id
                  WHERE r.created_at > NOW() - ($1 || ' minutes')::interval
                    AND r.available IN (TRUE, NULL)
                    AND s.is_active = TRUE
                  ORDER BY r.created_at DESC""",
                str(minutes),
            )
        return [dict(r) for r in rows]


async def get_subscribers_for_station(
    station_id: int,
    station_lat: float,
    station_lon: float,
    fuel_type: str,
    radius_km: int = 10,
) -> list:
    """Возвращает подписчиков, которых надо уведомить о наличии на АЗС.

    Возвращает [{user_id, telegram_id, distance_km, last_notified_at}].
    """
    if USE_SQLITE:
        async with _db.execute(
            """SELECT s.id AS sub_id, s.user_id, s.station_id, s.center_lat, s.center_lon,
                      s.radius_km, s.fuel_type, s.last_notified_at,
                      u.telegram_id
               FROM subscriptions s
               JOIN users u ON u.id = s.user_id
               WHERE s.is_active = 1
                 AND u.is_blocked = 0
                 AND (
                     s.station_id = ?
                     OR (s.center_lat IS NOT NULL
                         AND ABS(? - s.center_lat) < 1
                         AND ABS(? - s.center_lon) < 1)
                 )""",
            (station_id, station_lat, station_lon),
        ) as cur:
            rows = await cur.fetchall()
        results = []
        for row in rows:
            r = dict(row)
            # Точная подписка на АЗС
            if r.get("station_id") == station_id:
                r["distance_km"] = 0
                results.append(r)
                continue
            # Гео-подписка
            if r.get("center_lat") is not None and r.get("center_lon") is not None:
                d = _haversine_km(station_lat, station_lon, r["center_lat"], r["center_lon"])
                sub_radius = r.get("radius_km") or 5
                if d <= sub_radius:
                    r["distance_km"] = d
                    results.append(r)
        return results
    else:
        # Для PostgreSQL используем PostGIS или упрощённый bbox
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT s.id AS sub_id, s.user_id, s.station_id, s.center_lat, s.center_lon,
                         s.radius_km, s.fuel_type, s.last_notified_at,
                         u.telegram_id
                  FROM subscriptions s
                  JOIN users u ON u.id = s.user_id
                  WHERE s.is_active = TRUE
                    AND u.is_blocked = FALSE
                    AND (
                        s.station_id = $1
                        OR (s.center_lat IS NOT NULL
                            AND ABS($2 - s.center_lat) < 1
                            AND ABS($3 - s.center_lon) < 1)
                    )""",
                station_id, station_lat, station_lon,
            )
        results = []
        for row in rows:
            r = dict(row)
            if r.get("station_id") == station_id:
                r["distance_km"] = 0
                results.append(r)
                continue
            if r.get("center_lat") is not None and r.get("center_lon") is not None:
                d = _haversine_km(station_lat, station_lon, r["center_lat"], r["center_lon"])
                sub_radius = r.get("radius_km") or 5
                if d <= sub_radius:
                    r["distance_km"] = d
                    results.append(r)
        return results


async def mark_subscription_notified(sub_id: int) -> None:
    """Обновляет last_notified_at подписки."""
    now_iso = datetime.now().isoformat()
    if USE_SQLITE:
        await _db.execute(
            "UPDATE subscriptions SET last_notified_at = ? WHERE id = ?",
            (now_iso, sub_id),
        )
        await _db.commit()
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE subscriptions SET last_notified_at = NOW() WHERE id = $1",
                sub_id,
            )


# === Owner stations ===
async def add_owner_station(
    user_id: int,
    station_id: int,
    inn: str | None = None,
    role: str = "owner",
) -> int:
    """Регистрирует пользователя как владельца/работника АЗС.

    Создаёт запись с is_verified=False и помечает user.is_owner=1 в одной транзакции.
    Бейдж Verified появится только после модерации (set_owner_station_verified).
    Возвращает -1 если пользователь уже зарегистрирован на эту АЗС.
    """
    if USE_SQLITE:
        try:
            # BEGIN ... COMMIT — одна транзакция
            await _db.execute("BEGIN")
            async with _db.execute(
                """INSERT INTO owner_stations (user_id, station_id, inn, role, is_verified)
                   VALUES (?, ?, ?, ?, 0)""",
                (user_id, station_id, inn, role),
            ) as cur:
                row_id = cur.lastrowid
            await _db.execute(
                "UPDATE users SET is_owner = 1 WHERE id = ?",
                (user_id,),
            )
            await _db.commit()
            return row_id
        except Exception as e:
            await _db.rollback()
            if "UNIQUE" in str(e):
                return -1
            raise
    async with _db.acquire() as conn:
        async with conn.transaction():
            try:
                row = await conn.fetchrow(
                    """INSERT INTO owner_stations (user_id, station_id, inn, role, is_verified)
                       VALUES ($1, $2, $3, $4, FALSE)
                       RETURNING id""",
                    user_id, station_id, inn, role,
                )
                await conn.execute(
                    "UPDATE users SET is_owner = TRUE WHERE id = $1",
                    user_id,
                )
                return row["id"]
            except Exception as e:
                if "unique" in str(e).lower():
                    return -1
                raise


async def get_owner_stations(user_id: int) -> list:
    """Возвращает АЗС, на которые зарегистрирован пользователь как владелец/работник."""
    if USE_SQLITE:
        async with _db.execute(
            """SELECT os.id, os.station_id, os.role, os.is_verified, os.inn,
                      s.name, s.operator, s.city, s.address, s.lat, s.lon
               FROM owner_stations os
               JOIN stations s ON s.id = os.station_id
               WHERE os.user_id = ?
               ORDER BY s.name""",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT os.id, os.station_id, os.role, os.is_verified, os.inn,
                         s.name, s.operator, s.city, s.address, s.lat, s.lon
                  FROM owner_stations os
                  JOIN stations s ON s.id = os.station_id
                  WHERE os.user_id = $1
                  ORDER BY s.name""",
                user_id,
            )
        return [dict(r) for r in rows]


async def is_owner_of_station(user_id: int, station_id: int) -> bool:
    """Проверяет, является ли пользователь владельцем/работником АЗС."""
    if USE_SQLITE:
        async with _db.execute(
            "SELECT 1 FROM owner_stations WHERE user_id = ? AND station_id = ? LIMIT 1",
            (user_id, station_id),
        ) as cur:
            return (await cur.fetchone()) is not None
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM owner_stations WHERE user_id = $1 AND station_id = $2 LIMIT 1",
                user_id, station_id,
            )
            return row is not None


async def set_owner_station_verified(owner_station_id: int, moderator_id: int | None = None) -> None:
    """Модератор одобряет заявку. Также ставит is_verified на АЗС."""
    now_iso = datetime.now().isoformat()
    # Проверяем, что moderator_id существует (если передан)
    if moderator_id is not None:
        if USE_SQLITE:
            async with _db.execute(
                "SELECT 1 FROM users WHERE id = ?", (moderator_id,)
            ) as cur:
                if (await cur.fetchone()) is None:
                    moderator_id = None
        else:
            async with _db.acquire() as conn:
                row = await conn.fetchrow("SELECT 1 FROM users WHERE id = $1", moderator_id)
                if not row:
                    moderator_id = None

    if USE_SQLITE:
        async with _db.execute(
            "SELECT station_id FROM owner_stations WHERE id = ?",
            (owner_station_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return
        station_id = row[0]
        await _db.execute(
            """UPDATE owner_stations
               SET is_verified = 1, moderator_id = ?, verified_at = ?
               WHERE id = ?""",
            (moderator_id, now_iso, owner_station_id),
        )
        await _db.execute(
            "UPDATE stations SET is_verified = 1 WHERE id = ?",
            (station_id,),
        )
        await _db.commit()
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT station_id FROM owner_stations WHERE id = $1",
                owner_station_id,
            )
            if not row:
                return
            await conn.execute(
                """UPDATE owner_stations
                   SET is_verified = TRUE, moderator_id = $1, verified_at = NOW()
                   WHERE id = $2""",
                moderator_id, owner_station_id,
            )
            await conn.execute(
                "UPDATE stations SET is_verified = TRUE WHERE id = $1",
                row["station_id"],
            )


async def get_pending_owner_applications() -> list:
    """Заявки на модерацию (is_verified=0, ожидают одобрения)."""
    if USE_SQLITE:
        async with _db.execute(
            """SELECT os.id, os.user_id, os.station_id, os.inn, os.role, os.created_at,
                      u.telegram_id, u.first_name, u.username,
                      s.name AS station_name, s.city
               FROM owner_stations os
               JOIN users u ON u.id = os.user_id
               JOIN stations s ON s.id = os.station_id
               WHERE os.is_verified = 0
               ORDER BY os.created_at DESC""",
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT os.id, os.user_id, os.station_id, os.inn, os.role, os.created_at,
                         u.telegram_id, u.first_name, u.username,
                         s.name AS station_name, s.city
                  FROM owner_stations os
                  JOIN users u ON u.id = os.user_id
                  JOIN stations s ON s.id = os.station_id
                  WHERE os.is_verified = FALSE
                  ORDER BY os.created_at DESC""",
            )
        return [dict(r) for r in rows]
