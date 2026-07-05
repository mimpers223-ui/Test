"""
Экспорт данных из SQLite в PostgreSQL (Supabase).

Использование:
  1. Создай проект на https://supabase.com (free tier)
  2. Settings → Database → Connection string → URI
  3. export DATABASE_URL='postgresql://postgres:xxx@db.xxx.supabase.co:5432/postgres'
  4. Применить db/schema.sql в SQL Editor на Supabase
  5. pip install psycopg2-binary
  6. python scripts/sqlite_to_pg.py

Импортирует:
- stations (с city/address)
- users
- reports
- subscriptions
- owner_stations
- events
"""
import os
import sqlite3
import json
from datetime import datetime

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    print("Установи: pip install psycopg2-binary")
    raise

SQLITE_PATH = "bot/benzin.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL не задан. export DATABASE_URL='postgresql://...'")

# Supabase даёт URL вида postgresql://postgres:pass@db.xxx.supabase.co:5432/postgres
# Конвертируем в asyncpg / psycopg2 формат (если нужно)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

print(f"Читаем из: {SQLITE_PATH}")
print(f"Пишем в: {DATABASE_URL.split('@')[-1]}")

sq = sqlite3.connect(SQLITE_PATH)
sq.row_factory = sqlite3.Row
pg = psycopg2.connect(DATABASE_URL)
pg.autocommit = False
cur = pg.cursor()

def table_count(name):
    c = sq.execute(f"SELECT COUNT(*) FROM {name}")
    return c.fetchone()[0]

tables = ["users", "stations", "reports", "subscriptions", "owner_stations", "events", "user_badges", "premium_subscriptions"]
for t in tables:
    n = table_count(t)
    print(f"  {t}: {n} строк")

print()
print("Импорт...")

# === users ===
sq_users = sq.execute("SELECT * FROM users").fetchall()
if sq_users:
    rows = []
    for r in sq_users:
        rows.append((
            r["id"], r["telegram_id"], r["username"], r["first_name"], r["last_name"],
            r["language_code"], r["reputation"], r["total_reports"], r["confirmed_reports"],
            r["badge"], r["region"], r["city"], bool(r["is_owner"]), bool(r["is_blocked"]),
            r["created_at"], r["last_active_at"],
        ))
    execute_values(cur, """INSERT INTO users
        (id, telegram_id, username, first_name, last_name, language_code, reputation,
         total_reports, confirmed_reports, badge, region, city, is_owner, is_blocked,
         created_at, last_active_at)
        VALUES %s ON CONFLICT (id) DO NOTHING""", rows)
    print(f"  users: {len(rows)} импортировано")

# === stations ===
sq_st = sq.execute("SELECT * FROM stations").fetchall()
if sq_st:
    rows = []
    for r in sq_st:
        # Convert fuel_types from JSON string to PostgreSQL array format
        fuel_types_raw = r["fuel_types"]
        if fuel_types_raw:
            try:
                ft_list = json.loads(fuel_types_raw)
                ft_pg = "{" + ",".join(f'"{ft}"' for ft in ft_list) + "}"
            except (json.JSONDecodeError, TypeError):
                ft_pg = "{}"
        else:
            ft_pg = "{}"
        rows.append((
            r["id"], r["osm_id"], r["name"], r["operator"], r["brand"], r["network"],
            r["country"], r["region"], r["city"], r["address"],
            r["lat"], r["lon"], ft_pg,
            bool(r["has_24_7"]), r["phone"], r["website"], bool(r["is_verified"]), bool(r["is_active"]),
            r["created_at"], r["updated_at"],
        ))
    execute_values(cur, """INSERT INTO stations
        (id, osm_id, name, operator, brand, network, country, region, city, address,
         lat, lon, fuel_types, has_24_7, phone, website, is_verified, is_active,
         created_at, updated_at)
        VALUES %s ON CONFLICT (id) DO NOTHING""", rows)
    print(f"  stations: {len(rows)} импортировано")

# === reports ===
sq_rep = sq.execute("SELECT * FROM reports").fetchall()
if sq_rep:
    rows = []
    for r in sq_rep:
        rows.append((
            r["id"], r["station_id"], r["user_id"], r["fuel_type"], bool(r["available"]),
            r["price"], r["queue_size"], bool(r["has_limit"]), r["limit_liters"],
            r["comment"], r["confidence"], r["confirmations"], r["disputes"],
            r["source"], r["expires_at"], r["created_at"],
        ))
    execute_values(cur, """INSERT INTO reports
        (id, station_id, user_id, fuel_type, available, price, queue_size,
         has_limit, limit_liters, comment, confidence, confirmations, disputes,
         source, expires_at, created_at)
        VALUES %s ON CONFLICT (id) DO NOTHING""", rows)
    print(f"  reports: {len(rows)} импортировано")

# === subscriptions ===
sq_sub = sq.execute("SELECT * FROM subscriptions").fetchall()
if sq_sub:
    rows = []
    for r in sq_sub:
        rows.append((
            r["id"], r["user_id"], r["station_id"], r["city"], r["region"],
            r["fuel_type"], r["radius_km"], r["center_lat"], r["center_lon"],
            bool(r["is_active"]), r["last_notified_at"], r["created_at"],
        ))
    execute_values(cur, """INSERT INTO subscriptions
        (id, user_id, station_id, city, region, fuel_type, radius_km,
         center_lat, center_lon, is_active, last_notified_at, created_at)
        VALUES %s ON CONFLICT (id) DO NOTHING""", rows)
    print(f"  subscriptions: {len(rows)} импортировано")

# === owner_stations ===
sq_os = sq.execute("SELECT * FROM owner_stations").fetchall()
if sq_os:
    rows = []
    for r in sq_os:
        rows.append((
            r["id"], r["user_id"], r["station_id"], r["inn"], r["role"],
            bool(r["is_verified"]), r["moderator_id"], r["rejection_reason"],
            r["created_at"], r["verified_at"],
        ))
    execute_values(cur, """INSERT INTO owner_stations
        (id, user_id, station_id, inn, role, is_verified, moderator_id,
         rejection_reason, created_at, verified_at)
        VALUES %s ON CONFLICT (id) DO NOTHING""", rows)
    print(f"  owner_stations: {len(rows)} импортировано")

# === events ===
sq_ev = sq.execute("SELECT * FROM events").fetchall()
if sq_ev:
    rows = []
    for r in sq_ev:
        rows.append((
            r["id"], r["user_id"], r["event_type"], r["payload"], r["created_at"],
        ))
    execute_values(cur, """INSERT INTO events
        (id, user_id, event_type, payload, created_at)
        VALUES %s ON CONFLICT (id) DO NOTHING""", rows)
    print(f"  events: {len(rows)} импортировано")

# === user_badges ===
sq_ub = sq.execute("SELECT * FROM user_badges").fetchall()
if sq_ub:
    rows = []
    for r in sq_ub:
        rows.append((
            r["id"], r["user_id"], r["badge_code"], r["awarded_at"],
        ))
    execute_values(cur, """INSERT INTO user_badges
        (id, user_id, badge_code, awarded_at)
        VALUES %s ON CONFLICT (id) DO NOTHING""", rows)
    print(f"  user_badges: {len(rows)} импортировано")

# === Reset sequences ===
print()
print("Сброс sequences...")
for table in ["users", "stations", "reports", "subscriptions", "owner_stations", "events", "user_badges"]:
    cur.execute(f"""SELECT setval(pg_get_serial_sequence('{table}', 'id'),
        COALESCE((SELECT MAX(id) FROM {table}), 1))""")
print("  OK")

pg.commit()
print()
print("✅ Миграция завершена!")
print()
print("Следующие шаги:")
print("1. export DATABASE_URL='postgresql://...' (тот же URL)")
print("2. export USE_SQLITE=false")
print("3. В Render env: задай DATABASE_URL + USE_SQLITE=false")
print("4. Render пересоберётся автоматически")
