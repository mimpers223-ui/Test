"""
Обогащение адресов через Nominatim reverse geocoding.

Для всех АЗС в БД без адреса делает запрос к Nominatim
и сохраняет address + city + region.

⚠️ Ограничение Nominatim: 1 запрос/сек (free tier).

Использование:
  python scripts/enrich_addresses.py --limit 1000
  python scripts/enrich_addresses.py --limit 10000
"""
import argparse
import asyncio
import os
import sys
from typing import Optional

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402


NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "BenzinRyadom/1.0 (https://t.me/benzyn_ryadom)"


async def reverse_geocode(session: aiohttp.ClientSession, lat: float, lon: float) -> Optional[dict]:
    """Reverse geocoding через Nominatim."""
    try:
        params = {
            "format": "json",
            "lat": lat,
            "lon": lon,
            "accept-language": "ru",
            "zoom": "18",  # street level
        }
        headers = {"User-Agent": USER_AGENT}
        async with session.get(
            NOMINATIM_URL,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status == 200:
                data = await r.json()
                return data.get("address", {})
    except Exception as e:
        print(f"  ⚠ Nominatim: {e}")
    return None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000, help="Лимит АЗС")
    parser.add_argument("--rate", type=float, default=1.1, help="Запросов/сек")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"=== Обогащение адресов (Nominatim) ===")
    print(f"Лимит: {args.limit}, rate: {args.rate} req/s")

    if not args.dry_run:
        await db.init_db()

    # Загружаем АЗС без адреса
    rows = await db._fetch(f"""
        SELECT id, name, lat, lon
        FROM stations
        WHERE lat IS NOT NULL
          AND lon IS NOT NULL
          AND (address IS NULL OR address = '')
        ORDER BY id
        LIMIT {args.limit}
    """)
    print(f"Найдено АЗС без адреса: {len(rows)}")

    if not rows:
        print("Все АЗС уже имеют адреса!")
        return 0

    updated = 0
    errors = 0
    delay = 1.0 / args.rate

    async with aiohttp.ClientSession() as session:
        for i, st in enumerate(rows):
            sid = st["id"]
            name = st["name"]
            lat = st["lat"]
            lon = st["lon"]

            # Nominatim request
            addr = await reverse_geocode(session, lat, lon)
            if not addr:
                errors += 1
                await asyncio.sleep(delay)
                continue

            # Парсим
            street = (
                addr.get("road")
                or addr.get("pedestrian")
                or addr.get("footway")
                or ""
            )
            house = addr.get("house_number", "")
            city = (
                addr.get("city")
                or addr.get("town")
                or addr.get("village")
                or addr.get("hamlet")
                or addr.get("suburb")
                or ""
            )
            region = (
                addr.get("state")
                or addr.get("region")
                or ""
            )
            full_address = (
                f"{street} {house}".strip()
                if street or house
                else addr.get("display_name", "")[:200]
            )

            if not args.dry_run:
                try:
                    await db._execute(
                        """
                        UPDATE stations
                        SET address = $1, city = COALESCE(NULLIF($2, ''), city), region = COALESCE(NULLIF($3, ''), region)
                        WHERE id = $4
                        """,
                        full_address, city, region, sid,
                    )
                    updated += 1
                    if updated % 50 == 0:
                        print(f"  Обновлено: {updated}/{len(rows)} (errors: {errors})")
                except Exception as e:
                    errors += 1
                    print(f"  ⚠ Update {sid}: {e}")

            await asyncio.sleep(delay)

            if (i + 1) % 100 == 0:
                print(f"  Прогресс: {i+1}/{len(rows)} (updated: {updated}, errors: {errors})")

    print()
    print(f"=== Итого ===")
    print(f"  Обновлено: {updated}")
    print(f"  Ошибок: {errors}")
    if not args.dry_run:
        await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
