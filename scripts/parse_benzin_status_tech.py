"""
Парсер benzin-status.tech (Mini App для @benzin_status_bot).

Использует ПРЯМОЙ API Mini App — НЕ требует Telethon и авторизации.
Найден в JS-бандле: /api/search, /api/stations?bbox=, /api/stations/{id}

Источник данных: 685+ АЗС в Москве, реальный статус от водителей.

Пример:
  https://map.benzin-status.tech/api/search?q=Москва
  https://map.benzin-status.tech/api/stations?bbox=37.3,55.5,37.9,55.9
  https://map.benzin-status.tech/api/stations/1
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import aiohttp
from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent.parent / "bot" / ".env"
load_dotenv(ENV_PATH)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

API_BASE = "https://map.benzin-status.tech/api"

# Маппинг их fuel types → наши
FUEL_MAP = {
    "ai92":  "92",
    "ai95":  "95",
    "ai98":  "98",
    "ai100": "100",
    "dt":    "diesel",
    "gas":   "lpg",
}

# Маппинг их статусов → наши
STATUS_MAP = {
    "available":   True,
    "limited":     True,    # ограниченное количество, но есть
    "none":        False,   # нет в наличии
    "unknown":     None,
}

# Города-миллионники РФ (16)
DEFAULT_CITIES = [
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
    "Нижний Новгород", "Челябинск", "Самара", "Омск", "Ростов-на-Дону",
    "Уфа", "Красноярск", "Воронеж", "Волгоград", "Пермь", "Краснодар",
    "Иваново", "Тюмень", "Саратов", "Тольятти", "Барнаул", "Ижевск",
    "Хабаровск", "Владивосток", "Ярославль", "Томск", "Кемерово",
]

NETWORK_KEYWORDS = {
    "Лукойл":         ["лукойл", "lukoil"],
    "Газпромнефть":   ["газпромнефть", "газпром", "gazprom"],
    "Роснефть":       ["роснефть", "rosneft"],
    "Татнефть":       ["татнефть", "tatneft"],
    "Башнефть":       ["башнефть", "bashneft"],
    "Shell":          ["шелл", "shell"],
    "Нефтьмагистраль": ["нефтьмагистраль"],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("benzin_status_tech")


async def search_city(session, city: str) -> Optional[dict]:
    """Ищет город в API. Возвращает {name, lat, lng, count} или None."""
    try:
        async with session.get(
            f"{API_BASE}/search",
            params={"q": city},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://map.benzin-status.tech/"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                logger.warning("  [%s] HTTP %d", city, resp.status)
                return None
            data = await resp.json()
            cities = data.get("cities", [])
            # Ищем точное совпадение
            for c in cities:
                if c["name"].lower() == city.lower():
                    return c
            # Или первое
            if cities:
                return cities[0]
    except Exception as e:
        logger.warning("  [%s] search: %s", city, e)
    return None


async def fetch_stations_in_bbox(session, lat: float, lng: float, count: int) -> list:
    """Загружает все АЗС в bbox ~10км вокруг центра города.

    bbox формат: minLat,minLng,maxLat,maxLng (широта первой)
    """
    radius = 0.1  # ~11 км
    bbox = f"{lat - radius},{lng - radius},{lat + radius},{lng + radius}"
    try:
        async with session.get(
            f"{API_BASE}/stations",
            params={"bbox": bbox, "limit": min(count, 500)},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://map.benzin-status.tech/"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                logger.warning("  bbox HTTP %d", resp.status)
                return []
            data = await resp.json()
            return data.get("stations", [])
    except Exception as e:
        logger.warning("  bbox: %s", e)
    return []


async def fetch_station_detail(session, station_id: int) -> Optional[dict]:
    """Загружает детальную информацию об АЗС (reports, prices, queue)."""
    try:
        async with session.get(
            f"{API_BASE}/stations/{station_id}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://map.benzin-status.tech/"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as e:
        logger.debug("  detail %d: %s", station_id, e)
    return None


def find_network(text: str) -> Optional[str]:
    """Извлекает сеть из названия/адреса."""
    text_lower = text.lower() if text else ""
    for net, kws in NETWORK_KEYWORDS.items():
        if any(kw in text_lower for kw in kws):
            return net
    return None


async def find_station_in_db(network: Optional[str], name: Optional[str],
                             address: Optional[str], lat: float, lng: float) -> Optional[int]:
    """Ищет АЗС в нашей БД."""
    radius = 0.005  # ~500м
    if db.USE_SQLITE:
        # Сначала по координатам
        rows = await db._fetch(
            """SELECT id, name, operator, address FROM stations
               WHERE ABS(lat - ?) < ? AND ABS(lon - ?) < ?
               LIMIT 5""",
            lat, radius, lng, radius,
        )
    else:
        async with db._db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, name, operator, address FROM stations
                   WHERE ABS(lat - $1) < $2 AND ABS(lon - $3) < $2
                   LIMIT 5""",
                lat, radius, lng,
            )
    if not rows:
        return None
    # Сначала ищем с тем же оператором
    for r in rows:
        r = dict(r) if not isinstance(r, dict) else r
        op = (r.get("operator") or "").lower()
        nm = (r.get("name") or "").lower()
        if network and (network.lower() in op or network.lower() in nm):
            return r["id"]
    # Затем по адресу
    if address:
        for r in rows:
            r = dict(r) if not isinstance(r, dict) else r
            ad = (r.get("address") or "").lower()
            if address.lower()[:20] in ad:
                return r["id"]
    # Иначе — первая по координатам
    r = rows[0]
    return r["id"] if isinstance(r, dict) else r[0]


async def process_station(session, station_summary: dict, city: str) -> int:
    """Обрабатывает одну АЗС: получает детали, сохраняет отчёты в БД."""
    sid_external = station_summary.get("id")
    if not sid_external:
        return 0
    detail = await fetch_station_detail(session, sid_external)
    if not detail:
        return 0
    station = detail.get("station", {})
    lat = station.get("lat")
    lng = station.get("lng")
    if not lat or not lng:
        return 0

    network = find_network(station.get("brand") or "") or find_network(station.get("name") or "")
    address = station.get("address")
    name = station.get("name")

    station_id = await find_station_in_db(network, name, address, lat, lng)
    if not station_id:
        return 0

    saved = 0
    # Берём последние 5 отчётов (свежие имеют приоритет)
    reports = (detail.get("reports") or [])[:5]
    for rep in reports:
        if not rep.get("counted"):
            continue
        created_ms = rep.get("createdAt", 0)
        # Фильтр: только свежие отчёты (за последние 2 часа для парсеров)
        age_hours = (time.time() * 1000 - created_ms) / 1000 / 3600
        if age_hours > 24:
            continue  # слишком старые

        status = rep.get("status")
        available = STATUS_MAP.get(status)
        if available is None and not rep.get("fuelTypes"):
            continue
        price = rep.get("price")
        comment = rep.get("comment") or ""
        limit_liters = rep.get("limitLiters")
        has_limit = limit_liters is not None
        canister = rep.get("canister")
        if canister == "no":
            comment = "[канистры нет] " + comment
        elif canister == "yes":
            comment = "[канистры есть] " + comment

        fuel_types = rep.get("fuelTypes") or ["all"]
        if not fuel_types:
            fuel_types = ["all"]
        for ft in fuel_types:
            fuel_internal = FUEL_MAP.get(ft, ft)
            await db.add_report(
                station_id=station_id,
                fuel_type=fuel_internal,
                available=available,
                price=float(price) if price else None,
                source="benzin_status_tech",
                queue_size=None,
                has_limit=has_limit,
                limit_liters=int(limit_liters) if limit_liters else None,
                comment=comment[:200] if comment else f"benzin-status.tech #{sid_external}",
            )
            saved += 1
    return saved


async def parse_city(session, city: str) -> int:
    """Парсит один город."""
    logger.info("[%s] поиск...", city)
    city_info = await search_city(session, city)
    if not city_info:
        logger.info("  [%s] ❌ не найден", city)
        return 0
    lat = city_info.get("lat")
    lng = city_info.get("lng")
    count_expected = city_info.get("count", 0)
    logger.info("  центр (%.4f, %.4f), заявлено %d АЗС", lat, lng, count_expected)

    stations = await fetch_stations_in_bbox(session, lat, lng, count_expected)
    if not stations:
        logger.info("  ❌ нет АЗС в bbox")
        return 0
    logger.info("  получено %d АЗС из API", len(stations))

    # Bulk-получаем все наши АЗС в том же bbox (1 запрос вместо N)
    radius = 0.1
    min_lat, max_lat = lat - radius, lat + radius
    min_lng, max_lng = lng - radius, lng + radius
    logger.info("  bbox: lat[%.3f..%.3f] lon[%.3f..%.3f]", min_lat, max_lat, min_lng, max_lng)
    if db.USE_SQLITE:
        our_stations = await db._fetch(
            """SELECT id, lat, lon, operator, name, address FROM stations
               WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?""",
            min_lat, max_lat, min_lng, max_lng,
        )
    else:
        async with db._db.acquire() as conn:
            our_stations = await conn.fetch(
                """SELECT id, lat, lon, operator, name, address FROM stations
                   WHERE lat BETWEEN $1 AND $2 AND lon BETWEEN $3 AND $4""",
                min_lat, max_lat, min_lng, max_lng,
            )
    our_list = [dict(r) if not isinstance(r, dict) else r for r in our_stations]
    logger.info("  в нашей БД: %d АЗС в том же bbox", len(our_list))
    if our_list:
        logger.info("    пример: id=%d lat=%.4f lon=%.4f op=%s",
                    our_list[0]["id"], our_list[0]["lat"], our_list[0]["lon"],
                    (our_list[0].get("operator") or "")[:30])

    # Сопоставляем по координатам (O(N*M), но N,M < 1000 — быстро)
    def match_our(their_lat, their_lng):
        for ours in our_list:
            if abs(ours["lat"] - their_lat) < 0.005 and abs(ours["lon"] - their_lng) < 0.005:
                return ours
        return None

    saved = 0
    processed = 0
    matched = 0
    for st in stations[:30]:  # лимит 30 на город
        lat_st = st.get("lat")
        lng_st = st.get("lng")
        if not lat_st or not lng_st:
            continue
        processed += 1
        our = match_our(lat_st, lng_st)
        if not our:
            continue
        matched += 1
        try:
            # Получаем детали только для matching АЗС
            detail = await fetch_station_detail(session, st.get("id"))
            if not detail:
                continue
            station_obj = detail.get("station", {})
            count = await save_station_reports(our["id"], station_obj, detail)
            saved += count
        except Exception as e:
            logger.debug("  station %s: %s", st.get("id"), e)
        await asyncio.sleep(0.15)  # rate limit

    logger.info("  processed: %d, matched: %d, сохранено: %d", processed, matched, saved)
    return saved


async def save_station_reports(station_id: int, station_obj: dict, detail: dict) -> int:
    """Сохраняет все отчёты об АЗС в БД."""
    saved = 0
    reports = (detail.get("reports") or [])[:5]
    for rep in reports:
        if not rep.get("counted"):
            continue
        created_ms = rep.get("createdAt", 0)
        age_hours = (time.time() * 1000 - created_ms) / 1000 / 3600
        if age_hours > 24:
            continue
        status = rep.get("status")
        available = STATUS_MAP.get(status)
        if available is None and not rep.get("fuelTypes"):
            continue
        price = rep.get("price")
        comment = rep.get("comment") or ""
        limit_liters = rep.get("limitLiters")
        has_limit = limit_liters is not None
        canister = rep.get("canister")
        if canister == "no":
            comment = "[канистры нет] " + comment
        elif canister == "yes":
            comment = "[канистры есть] " + comment

        fuel_types = rep.get("fuelTypes") or ["all"]
        if not fuel_types:
            fuel_types = ["all"]
        for ft in fuel_types:
            fuel_internal = FUEL_MAP.get(ft, ft)
            try:
                await db.add_report(
                    station_id=station_id,
                    fuel_type=fuel_internal,
                    available=available,
                    price=float(price) if price else None,
                    source="benzin_status_tech",
                    queue_size=None,
                    has_limit=has_limit,
                    limit_liters=int(limit_liters) if limit_liters else None,
                    comment=comment[:200] if comment else None,
                )
                saved += 1
            except Exception as e:
                logger.debug("add_report error: %s", e)
    return saved


async def run(cities: list[str]) -> int:
    logger.info("=== Парсер benzin-status.tech (%d городов) ===", len(cities))
    logger.info("  USE_SQLITE=%s _API_MODE=%s", db.USE_SQLITE, db.API_MODE)
    if not db.API_MODE:
        await db.init_db()
    await db.stale_old_reports("benzin_status_tech")

    total = 0
    async with aiohttp.ClientSession() as session:
        for city in cities:
            try:
                count = await parse_city(session, city)
                logger.info("  ✅ [%s] сохранено: %d", city, count)
                total += count
            except Exception as e:
                logger.warning("  [%s] ошибка: %s", city, e, exc_info=True)
            await asyncio.sleep(1)  # rate limit между городами

    if not db.API_MODE:
        await db.close_db()
    logger.info("=== Total: %d отчётов ===", total)
    return total


def main():
    parser = argparse.ArgumentParser(description="Парсер benzin-status.tech Mini App API")
    parser.add_argument("--cities", default=",".join(DEFAULT_CITIES),
                        help="Города через запятую")
    args = parser.parse_args()
    cities = [c.strip() for c in args.cities.split(",") if c.strip()]
    asyncio.run(run(cities))


if __name__ == "__main__":
    main()
