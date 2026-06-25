"""
Парсер АЗС из OpenStreetMap через Overpass API.
Загружает все заправки России с тегами топлива.
Работает с SQLite (локально) и PostgreSQL (Supabase).

Использование:
    python parse_osm.py
"""
import asyncio
import json
import math
import os
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

# Подавляем шум от aiohttp ssl
import warnings
warnings.filterwarnings("ignore")

# Загружаем .env из backend/
ENV_PATH = Path(__file__).parent.parent / "backend" / ".env"
load_dotenv(ENV_PATH)

USE_SQLITE = os.getenv("USE_SQLITE", "true").lower() == "true"
DB_PATH = Path(__file__).parent.parent / "bot" / "benzin.db"
DATABASE_URL = os.getenv("DATABASE_URL", "")

if not USE_SQLITE and not DATABASE_URL:
    print("ERROR: DATABASE_URL не задан")
    sys.exit(1)

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Запрос к Overpass API — все АЗС России с тегами топлива
QUERY_RUSSIA = """
[out:json][timeout:600];
area["ISO3166-1"="RU"]->.r;
(
  node["amenity"="fuel"](area.r);
  way["amenity"="fuel"](area.r);
  relation["amenity"="fuel"](area.r);
);
out center;
"""


def parse_fuel_tags(tags: dict) -> str:
    """Извлекаем типы топлива из тегов OSM, возвращаем JSON-массив строкой."""
    fuels = []
    mapping = {
        "fuel:octane_92": "92",
        "fuel:octane_95": "95",
        "fuel:octane_98": "98",
        "fuel:octane_100": "100",
        "fuel:diesel": "diesel",
        "fuel:lpg": "lpg",
        "fuel:cng": "cng",
        "fuel:electricity": "electro",
    }
    for tag, name in mapping.items():
        if tags.get(tag) == "yes":
            fuels.append(name)
    return json.dumps(fuels, ensure_ascii=False)


def parse_station(elem: dict) -> dict | None:
    """Превращает OSM-элемент в нашу запись."""
    tags = elem.get("tags", {})
    if "center" in elem:
        lat = elem["center"].get("lat")
        lon = elem["center"].get("lon")
    else:
        lat = elem.get("lat")
        lon = elem.get("lon")
    if not lat or not lon:
        return None

    # Собираем адрес из разных тегов
    addr_parts = []
    addr_street = tags.get("addr:street")
    addr_house = tags.get("addr:housenumber")
    if addr_street and addr_house:
        addr_parts.append(f"{addr_street}, {addr_house}")
    elif addr_street:
        addr_parts.append(addr_street)
    elif addr_house:
        addr_parts.append(addr_house)
    address = ", ".join(addr_parts) or tags.get("addr:full") or ""

    # Город — fallback если нет адреса
    city = (
        tags.get("addr:city")
        or tags.get("city")
        or tags.get("place")
        or tags.get("addr:town")
        or tags.get("addr:village")
        or ""
    )

    # Финальный "отображаемый адрес"
    if address and city:
        display_address = f"{city}, {address}"
    elif address:
        display_address = address
    elif city:
        display_address = city
    else:
        display_address = ""

    return {
        "osm_id": int(elem["id"]),
        "name": tags.get("name", tags.get("brand", "АЗС")),
        "operator": tags.get("operator"),
        "brand": tags.get("brand"),
        "network": tags.get("network"),
        "country": "RU",
        "region": tags.get("addr:region") or tags.get("region"),
        "city": city,
        "address": display_address,
        "lat": float(lat),
        "lon": float(lon),
        "fuel_types": parse_fuel_tags(tags),
        "has_24_7": 1 if tags.get("opening_hours") == "24/7" else 0,
        "phone": tags.get("phone") or tags.get("contact:phone"),
        "website": tags.get("website") or tags.get("contact:website"),
    }


async def fetch_overpass(session: aiohttp.ClientSession, query: str, attempt: int = 0) -> list:
    """Запрос к Overpass API с retry."""
    url = OVERPASS_URLS[attempt % len(OVERPASS_URLS)]
    print(f"  Запрос к {url}...")
    try:
        async with session.post(
            url,
            data={"data": query},
            timeout=aiohttp.ClientTimeout(total=600),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise aiohttp.ClientError(f"HTTP {resp.status}: {text[:200]}")
            data = await resp.json()
            return data.get("elements", [])
    except Exception as e:
        print(f"  Ошибка: {str(e)[:150]}")
        if attempt < len(OVERPASS_URLS) - 1:
            await asyncio.sleep(2)
            return await fetch_overpass(session, query, attempt + 1)
        return []


async def main():
    print("=" * 60)
    print("Парсер АЗС из OpenStreetMap")
    print(f"Режим: {'SQLite (локально)' if USE_SQLITE else 'PostgreSQL'}")
    print("=" * 60)

    # Подключаемся к БД
    print("\n[1/3] Подключение к БД...")

    if USE_SQLITE:
        import aiosqlite
        conn = await aiosqlite.connect(str(DB_PATH))
        print(f"  SQLite: {DB_PATH}")
    else:
        import asyncpg
        conn = await asyncpg.connect(DATABASE_URL, ssl="require")
        print(f"  PostgreSQL: {DATABASE_URL[:50]}...")

    # Создаём схему если её нет
    print("\n[2/3] Создание схемы (если нужно)...")
    if USE_SQLITE:
        schema_path = Path(__file__).parent.parent / "db" / "schema_sqlite.sql"
        schema_sql = schema_path.read_text(encoding="utf-8")
        await conn.executescript(schema_sql)
    else:
        schema_path = Path(__file__).parent.parent / "db" / "schema.sql"
        schema_sql = schema_path.read_text(encoding="utf-8")
        await conn.execute(schema_sql)
    print("  OK")

    # Запрашиваем OSM
    print("\n[3/3] Запрос к Overpass API (по всей России)...")
    print("  Это займёт 2–5 минут...")
    async with aiohttp.ClientSession() as session:
        elements = await fetch_overpass(session, QUERY_RUSSIA)
    print(f"  Получено элементов: {len(elements)}")

    # Парсим
    stations = [s for s in (parse_station(e) for e in elements) if s]
    print(f"  Валидных станций: {len(stations)}")

    # Вставка батчами
    print("\n  Запись в БД...")
    inserted = 0
    updated = 0
    BATCH = 200

    for i in range(0, len(stations), BATCH):
        batch = stations[i:i + BATCH]
        for s in batch:
            try:
                if USE_SQLITE:
                    await conn.execute(
                        """INSERT INTO stations (
                            osm_id, name, operator, brand, network, country,
                            region, city, address, lat, lon, fuel_types,
                            has_24_7, phone, website, is_active
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(osm_id) DO UPDATE SET
                            name=excluded.name, operator=excluded.operator,
                            brand=excluded.brand, network=excluded.network,
                            city=excluded.city, address=excluded.address,
                            lat=excluded.lat, lon=excluded.lon,
                            fuel_types=excluded.fuel_types,
                            has_24_7=excluded.has_24_7,
                            phone=excluded.phone, website=excluded.website,
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (s["osm_id"], s["name"], s["operator"], s["brand"],
                         s["network"], s["country"], s["region"], s["city"],
                         s["address"], s["lat"], s["lon"], s["fuel_types"],
                         s["has_24_7"], s["phone"], s["website"]),
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO stations (
                            osm_id, name, operator, brand, network, country,
                            region, city, address, lat, lon, fuel_types,
                            has_24_7, phone, website
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9,
                            $10, $11, $12, $13, $14, $15
                        )
                        ON CONFLICT (osm_id) DO UPDATE SET
                            name = EXCLUDED.name,
                            operator = EXCLUDED.operator,
                            brand = EXCLUDED.brand,
                            network = EXCLUDED.network,
                            city = EXCLUDED.city,
                            address = EXCLUDED.address,
                            lat = EXCLUDED.lat,
                            lon = EXCLUDED.lon,
                            fuel_types = EXCLUDED.fuel_types,
                            has_24_7 = EXCLUDED.has_24_7,
                            phone = EXCLUDED.phone,
                            website = EXCLUDED.website,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        s["osm_id"], s["name"], s["operator"], s["brand"],
                        s["network"], s["country"], s["region"], s["city"],
                        s["address"], s["lat"], s["lon"], s["fuel_types"],
                        s["has_24_7"], s["phone"], s["website"],
                    )
                inserted += 1
            except Exception as e:
                print(f"  Ошибка {s['osm_id']}: {str(e)[:100]}")

        if i % 1000 == 0:
            print(f"  Обработано {min(i + BATCH, len(stations))}/{len(stations)}")

    if USE_SQLITE:
        await conn.commit()
    print(f"\n  Обработано {len(stations)}/{len(stations)}")

    # Статистика
    print("\n" + "=" * 60)
    print("ТОП-10 ОПЕРАТОРОВ")
    print("=" * 60)
    if USE_SQLITE:
        async with conn.execute(
            """SELECT operator, COUNT(*) as cnt FROM stations
               WHERE operator IS NOT NULL AND operator != ''
               GROUP BY operator ORDER BY cnt DESC LIMIT 10"""
        ) as cur:
            rows = await cur.fetchall()
    else:
        rows = await conn.fetch("""
            SELECT operator, COUNT(*) as cnt FROM stations
            WHERE operator IS NOT NULL AND operator != ''
            GROUP BY operator ORDER BY cnt DESC LIMIT 10
        """)
    for row in rows:
        op = row[0] if USE_SQLITE else row["operator"]
        cnt = row[1] if USE_SQLITE else row["cnt"]
        print(f"  {op:40s} {cnt:>6,}")

    print("\n" + "=" * 60)
    print("ТОП-15 РЕГИОНОВ")
    print("=" * 60)
    if USE_SQLITE:
        async with conn.execute(
            """SELECT region, COUNT(*) as cnt FROM stations
               WHERE region IS NOT NULL AND region != ''
               GROUP BY region ORDER BY cnt DESC LIMIT 15"""
        ) as cur:
            rows = await cur.fetchall()
    else:
        rows = await conn.fetch("""
            SELECT region, COUNT(*) as cnt FROM stations
            WHERE region IS NOT NULL AND region != ''
            GROUP BY region ORDER BY cnt DESC LIMIT 15
        """)
    for row in rows:
        r = row[0] if USE_SQLITE else row["region"]
        cnt = row[1] if USE_SQLITE else row["cnt"]
        print(f"  {r:40s} {cnt:>6,}")

    print("\n" + "=" * 60)
    print(f"ИТОГО записано: {inserted}")
    print("=" * 60)

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
