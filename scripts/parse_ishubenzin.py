#!/usr/bin/env python3
"""
Парсер ishubenzin.ru — народная карта топлива.
API полностью бесплатный, ключ не нужен.
Формат: GET /api/stations?bbox=south,west,north,east

Статусы燃料:
  green  → есть (available=True)
  red    → нет (available=False)
  yellow → вопрос/очередь
  gray   → нет данных

markerColor станции:
  green  → все ок
  red    → дефицит
  yellow → проблема/очередь
  gray   → нет данных
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "bot" / ".env")
sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
from db import init_db, close_db, add_report, find_stations_by_city, upsert_station_for_import, stale_old_reports

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ishubenzin")

# Города/регионы для сканирования
SCAN_REGIONS = [
    # Города (маленький bbox 0.1°)
    ("Иваново", 57.0, 41.0, 57.15, 41.2),
    ("Москва", 55.55, 37.35, 55.95, 37.85),
    ("Санкт-Петербург", 59.75, 30.0, 60.1, 30.6),
    ("Владимир", 56.1, 40.35, 56.2, 40.5),
    ("Кострома", 57.7, 40.9, 57.8, 41.05),
    ("Ярославль", 57.55, 39.8, 57.75, 40.05),
    ("Тверь", 56.85, 35.85, 56.95, 36.0),
    ("Рыбинск", 58.0, 38.7, 58.1, 38.9),
    ("Шуя", 56.85, 41.35, 56.9, 41.45),
    ("Кинешма", 57.4, 42.1, 57.5, 42.25),
    ("Нижний Новгород", 56.25, 43.8, 56.4, 44.05),
    ("Волгоград", 48.65, 44.45, 48.85, 44.6),
    ("Екатеринбург", 56.75, 60.5, 56.9, 60.7),
    ("Казань", 55.75, 49.05, 55.85, 49.25),
    ("Новосибирск", 54.95, 82.85, 55.1, 83.1),
    ("Краснодар", 45.0, 38.9, 45.1, 39.1),
    ("Ростов-на-Дону", 47.2, 39.65, 47.3, 39.8),
    ("Самара", 53.15, 50.05, 53.3, 50.3),
    ("Уфа", 54.7, 55.9, 54.85, 56.15),
    ("Челябинск", 55.1, 61.35, 55.25, 61.5),
    ("Омск", 54.9, 73.3, 55.05, 73.5),
    ("Пермь", 57.95, 55.95, 58.1, 56.2),
    ("Красноярск", 55.95, 92.8, 56.15, 93.05),
    ("Воронеж", 51.6, 39.15, 51.75, 39.3),
    ("Саратов", 51.5, 46.0, 51.65, 46.2),
    ("Тюмень", 57.1, 65.5, 57.2, 65.7),
    ("Липецк", 52.6, 39.55, 52.7, 39.7),
    ("Тула", 54.15, 37.55, 54.25, 37.7),
    ("Калуга", 54.5, 36.2, 54.6, 36.35),
    ("Брянск", 53.2, 34.3, 53.35, 34.45),
]

# Статусы → available
STATUS_MAP = {
    "green": True,
    "red": False,
    "yellow": None,  # неизвестно
    "gray": None,
}

# Типы топлива → normalize
FUEL_MAP = {
    "АИ-92": "92",
    "АИ-95": "95",
    "АИ-98": "98",
    "АИ-100": "100",
    "ДТ": "diesel",
    "Газ": "gas",
}


async def fetch_region(session: aiohttp.ClientSession, name: str,
                       lat1: float, lon1: float, lat2: float, lon2: float) -> list:
    """Получает станции из региона по bbox."""
    bbox = f"{lat1},{lon1},{lat2},{lon2}"
    url = f"https://ishubenzin.ru/api/stations?bbox={bbox}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning(f"  {name}: HTTP {resp.status}")
                return []
            data = await resp.json()
            logger.info(f"  {name}: {len(data)} stations")
            return data
    except Exception as e:
        logger.error(f"  {name}: {e}")
        return []


def convert_station(raw: dict) -> dict | None:
    """Конвертирует станцию ishubenzin в наш формат."""
    lat = raw.get("lat")
    lon = raw.get("lon")
    if not lat or not lon:
        return None

    # Парсим fuel statuses → reports
    reports = []
    for fuel in raw.get("fuels", []):
        fuel_type_raw = fuel.get("type", "")
        fuel_type = FUEL_MAP.get(fuel_type_raw, fuel_type_raw)
        status = fuel.get("status", "gray")
        available = STATUS_MAP.get(status)
        price = fuel.get("price")

        if available is None:
            continue  # пропускаем gray/unknown

        reports.append({
            "fuel_type": fuel_type,
            "available": available,
            "price": price,
            "comment": f"ishubenzin.ru: status={status}",
        })

    # Определяем маркер
    marker = raw.get("markerColor", "gray")
    name = raw.get("name") or f"АЗС ({lat:.4f}, {lon:.4f})"

    return {
        "lat": lat,
        "lon": lon,
        "name": name,
        "markerColor": marker,
        "reports": reports,
    }


async def main():
    import os
    logger.info("=== ishubenzin.ru parser ===")
    if not db.API_MODE:
        await init_db()
    await stale_old_reports("ishubenzin")
    logger.info("DB ready")

    total_reports = 0
    total_stations = 0

    async with aiohttp.ClientSession() as session:
        for name, lat1, lon1, lat2, lon2 in SCAN_REGIONS:
            raw_stations = await fetch_region(session, name, lat1, lon1, lat2, lon2)

            for raw in raw_stations:
                station = convert_station(raw)
                if not station or not station["reports"]:
                    continue

                # Ищем станцию в БД по координатам (±0.01°)
                existing = await find_stations_by_city(name)
                matched_id = None
                if existing:
                    for s in existing:
                        if abs(s["lat"] - station["lat"]) < 0.01 and abs(s["lon"] - station["lon"]) < 0.01:
                            matched_id = s["id"]
                            break

                if not matched_id:
                    # Импортируем новую станцию
                    matched_id = await upsert_station_for_import(
                        name=station["name"],
                        region=name,
                        city=name,
                        lat=station["lat"],
                        lon=station["lon"],
                    )

                if not matched_id:
                    continue

                # Добавляем отчёты
                for r in station["reports"]:
                    await add_report(
                        station_id=matched_id,
                        fuel_type=r["fuel_type"],
                        available=r["available"],
                        price=r["price"],
                        comment=r["comment"],
                        source="ishubenzin",
                    )
                    total_reports += 1

                total_stations += 1
                await asyncio.sleep(0.2)  # не долбить сервер

    import os
    if not db.API_MODE:
        await close_db()
    logger.info(f"Done! Stations: {total_stations}, Reports: {total_reports}")


if __name__ == "__main__":
    asyncio.run(main())
