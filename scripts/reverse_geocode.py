"""
Reverse geocoding для станций без адреса.
Использует Nominatim (OpenStreetMap) — бесплатно, 1 req/sec.
"""
import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path

import aiohttp
import warnings
warnings.filterwarnings("ignore")

DB_PATH = Path(__file__).parent.parent / "bot" / "benzin.db"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"


async def reverse_geocode(session, lat, lon):
    """Получает адрес по координатам через Nominatim."""
    params = {
        "lat": lat,
        "lon": lon,
        "format": "json",
        "accept-language": "ru",
        "zoom": 18,
        "addressdetails": 1,
    }
    headers = {"User-Agent": "BenzinRyadomApp/1.0 (contact@benzin.app)"}
    try:
        async with session.get(
            NOMINATIM_URL, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data
    except Exception as e:
        return None


def format_address(data):
    """Форматирует адрес из ответа Nominatim."""
    if not data or "address" not in data:
        return None
    addr = data["address"]
    parts = []
    # Город/поселок
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("hamlet")
        or addr.get("suburb")
    )
    if city:
        parts.append(f"г. {city}")
    # Улица
    street = (
        addr.get("street")
        or addr.get("road")
        or addr.get("pedestrian")
        or addr.get("path")
    )
    if street:
        parts.append(street)
    # Дом
    house = addr.get("house_number")
    if house:
        parts.append(f"д. {house}")
    if not parts:
        # Fallback — display_name
        return data.get("display_name", "").split(",")[0:3]
    return ", ".join(parts)


async def main():
    print("=" * 60)
    print("Reverse geocoding для станций без адреса")
    print("=" * 60)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Берём станции без адреса (или с очень коротким)
    rows = conn.execute("""
        SELECT id, lat, lon, name
        FROM stations
        WHERE is_active = 1
        AND (address IS NULL OR address = '' OR LENGTH(address) < 5)
        ORDER BY id
        LIMIT 1000
    """).fetchall()

    print(f"Найдено станций без адреса: {len(rows)}")
    print(f"Обработка займёт ~{len(rows) * 1.1 / 60:.1f} минут\n")

    updated = 0
    async with aiohttp.ClientSession() as session:
        for i, row in enumerate(rows):
            addr = await reverse_geocode(session, row["lat"], row["lon"])
            formatted = format_address(addr)
            if formatted:
                conn.execute(
                    "UPDATE stations SET address = ? WHERE id = ?",
                    (formatted, row["id"]),
                )
                updated += 1
                if updated % 50 == 0:
                    conn.commit()
                    print(f"  [{i+1}/{len(rows)}] Обработано, обновлено: {updated}")
            # Rate limit — 1 req/sec
            await asyncio.sleep(1.1)

    conn.commit()
    print(f"\n✅ Обновлено станций: {updated} из {len(rows)}")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
