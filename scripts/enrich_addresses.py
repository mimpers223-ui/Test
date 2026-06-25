"""
Обогащение АЗС: reverse geocoding через Nominatim.
Для АЗС без адреса запрашивает Nominatim и обновляет БД.

⚠️ Nominatim rate limit: 1 запрос/сек. Для 26К АЗС ≈ 7 часов.
Для 100 АЗС (например, Иваново) — 100 секунд.

Использование:
    python enrich_addresses.py               # все АЗС без адреса
    python enrich_addresses.py --city Иваново   # только этот город
    python enrich_addresses.py --limit 100  # первые 100
    python enrich_addresses.py --dry-run    # только показать, без записи
"""
import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import aiohttp

# Загружаем .env из backend/
ENV_PATH = Path(__file__).parent.parent / "backend" / ".env"
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)
except ImportError:
    pass

# Импортируем после sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
import db  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("enrich")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "BenzinRyadomBot/1.0 (contact: t.me/benzyn_ryadom_bot)"
RATE_LIMIT_SEC = 1.1  # 1.1 сек — гарантируем < 1 req/sec


async def reverse_geocode(session: aiohttp.ClientSession, lat: float, lon: float) -> dict | None:
    """Обратный геокодинг через Nominatim."""
    params = {
        "lat": lat,
        "lon": lon,
        "format": "json",
        "accept-language": "ru",
        "zoom": 18,  # уровень здания
        "addressdetails": 1,
    }
    headers = {"User-Agent": USER_AGENT}
    try:
        async with session.get(
            NOMINATIM_URL, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status != 200:
                logger.warning("Nominatim %d for (%s, %s)", resp.status, lat, lon)
                return None
            data = await resp.json()
            return data
    except Exception as e:
        logger.warning("Reverse geocode failed for (%s, %s): %s", lat, lon, e)
        return None


def parse_nominatim(data: dict) -> dict:
    """Извлекает адрес, город, регион из Nominatim ответа."""
    addr = data.get("address", {})
    return {
        "address": _format_address(addr),
        "city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet") or "",
        "region": addr.get("state") or "",
    }


def _format_address(addr: dict) -> str:
    """Форматирует полный адрес из полей Nominatim."""
    parts = []
    # Улица + дом
    road = addr.get("road") or addr.get("pedestrian") or addr.get("footway")
    if road:
        house = addr.get("house_number")
        if house:
            parts.append(f"{road}, {house}")
        else:
            parts.append(road)

    # Микрорайон
    suburb = addr.get("suburb") or addr.get("neighbourhood")
    if suburb:
        parts.insert(0, suburb)

    return ", ".join(parts)


async def get_stations_in_bbox(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float, limit: int | None = None
) -> list:
    """Возвращает АЗС в bbox (lat/lon диапазон), даже если city не указан."""
    if db.USE_SQLITE:
        sql = """SELECT id, name, lat, lon, address, city, region
                 FROM stations
                 WHERE is_active = 1
                   AND lat BETWEEN ? AND ?
                   AND lon BETWEEN ? AND ?
                   AND (address IS NULL OR address = '' OR city IS NULL OR city = '')"""
        params = [lat_min, lat_max, lon_min, lon_max]
        sql += " ORDER BY id LIMIT ?"
        params.append(limit or 5000)
        async with db._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    async with db._db.acquire() as conn:
        sql = f"""SELECT id, name, lat, lon, address, city, region
                  FROM stations
                  WHERE is_active = TRUE
                    AND lat BETWEEN $1 AND $2
                    AND lon BETWEEN $3 AND $4
                    AND (address IS NULL OR address = '' OR city IS NULL OR city = '')
                  ORDER BY id LIMIT {limit or 5000}"""
        rows = await conn.fetch(sql, lat_min, lat_max, lon_min, lon_max)
        return [dict(r) for r in rows]


async def process_station(session, station: dict, dry_run: bool = False) -> bool:
    """Обогащает одну АЗС. Возвращает True если обновлено."""
    sid = station["id"]
    lat = station["lat"]
    lon = station["lon"]
    cur_addr = (station.get("address") or "").strip()
    cur_city = (station.get("city") or "").strip()

    if cur_addr and cur_city:
        return False  # уже обогащено

    data = await reverse_geocode(session, lat, lon)
    if not data:
        return False

    parsed = parse_nominatim(data)

    new_addr = parsed["address"] or cur_addr
    new_city = parsed["city"] or cur_city
    new_region = parsed["region"] or station.get("region") or ""

    if not new_addr and not new_city:
        return False

    if dry_run:
        logger.info(f"[DRY] #{sid} \"{station.get('name')}\": addr=\"{new_addr}\" city=\"{new_city}\"")
        return True

    await db.update_station_address(
        station_id=sid,
        address=new_addr,
        city=new_city,
        region=new_region,
    )
    logger.info(f"#{sid} \"{station.get('name')}\": addr=\"{new_addr}\" city=\"{new_city}\"")
    return True


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", help="Только этот город")
    parser.add_argument("--bbox", help="Bbox как 'lat_min,lat_max,lon_min,lon_max'")
    parser.add_argument("--limit", type=int, help="Макс. количество")
    parser.add_argument("--dry-run", action="store_true", help="Без записи в БД")
    parser.add_argument("--skip-existing", action="store_true", default=True, help="Пропускать АЗС с адресом")
    args = parser.parse_args()

    await db.init_db()

    # Получаем АЗС для обработки
    if args.bbox:
        parts = [float(x) for x in args.bbox.split(",")]
        if len(parts) != 4:
            logger.error("--bbox должен быть 'lat_min,lat_max,lon_min,lon_max'")
            return
        lat_min, lat_max, lon_min, lon_max = parts
        stations = await get_stations_in_bbox(lat_min, lat_max, lon_min, lon_max, limit=args.limit)
    elif args.city:
        stations = await db.get_stations_without_address(city=args.city, limit=args.limit)
    else:
        stations = await db.get_stations_without_address(limit=args.limit)

    logger.info(f"Найдено {len(stations)} АЗС для обогащения")

    if not stations:
        await db.close_db()
        return

    total_updated = 0
    async with aiohttp.ClientSession() as session:
        for i, s in enumerate(stations, 1):
            try:
                if await process_station(session, s, dry_run=args.dry_run):
                    total_updated += 1
            except Exception as e:
                logger.exception(f"Ошибка при обработке #{s['id']}: {e}")

            # Rate limit: 1 запрос в секунду
            if i < len(stations):
                await asyncio.sleep(RATE_LIMIT_SEC)

            if i % 50 == 0:
                logger.info(f"  Прогресс: {i}/{len(stations)}, обновлено: {total_updated}")

    logger.info(f"Готово. Обновлено: {total_updated}/{len(stations)}")
    await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
