#!/usr/bin/env python3
"""
Импорт ВСЕХ АЗС Ивановской области из OpenStreetMap через Overpass API.
Overpass API: https://overpass-api.de/api/interpreter

Парсит amenity=fuel в области и загружает в БД.
Идемпотентно — повторный запуск не дублирует.
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
import urllib.request
import urllib.parse

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "bot", ".env"))

import db
from db import _fetch, _execute

# === Ивановская область (bbox: 55.5,39.5,57.5,43.5) ===
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
BBOX = "55.5,39.5,57.5,43.5"  # south, west, north, east
REGION_NAME = "Ивановская область"

OVERPASS_QUERY = f"""
[out:json][timeout:90];
(
  node["amenity"="fuel"]({BBOX});
  way["amenity"="fuel"]({BBOX});
);
out center tags;
"""


def fetch_overpass() -> dict:
    """Запрашивает все АЗС в bbox у Overpass API."""
    data = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode("utf-8")
    req = urllib.request.Request(OVERPASS_URL, data=data, headers={
        "User-Agent": "benzin-ryadom/1.0 (https://t.me/benzyn_ryadom)"
    })
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read())


def osm_tags_to_station(elem: dict) -> dict:
    """Превращает OSM-элемент в нашу модель станции."""
    tags = elem.get("tags", {})
    center = elem.get("center", {})  # для way
    lat = elem.get("lat") or center.get("lat")
    lon = elem.get("lon") or center.get("lon")

    if not lat or not lon:
        return None

    # Название и оператор
    name = tags.get("name") or tags.get("brand") or "АЗС"
    operator = tags.get("operator") or tags.get("brand")

    # Адрес
    addr = tags.get("addr:street", "")
    if tags.get("addr:housenumber"):
        addr += f", {tags['addr:housenumber']}"
    if not addr:
        addr = tags.get("addr:full", "")

    # Город
    city = tags.get("addr:city") or tags.get("is_in:city") or ""

    return {
        "name": name[:200],
        "operator": (operator or "")[:200],
        "city": city[:100],
        "address": addr[:200],
        "lat": lat,
        "lon": lon,
    }


async def import_to_db(stations: list) -> tuple[int, int, int]:
    """
    Импортирует станции в БД.
    Возвращает (total, added, updated).
    """
    added = 0
    updated = 0

    for s in stations:
        if not s.get("lat") or not s.get("lon"):
            continue

        # Проверяем, есть ли уже такая станция (по координатам ±50м)
        if db.USE_SQLITE:
            existing = await _fetch(
                "SELECT id, name, operator, city, address FROM stations "
                "WHERE ABS(lat - ?) < 0.0005 AND ABS(lon - ?) < 0.0005 LIMIT 1",
                s["lat"], s["lon"],
                one=True,
            )
        else:
            existing = await _fetch(
                "SELECT id, name, operator, city, address FROM stations "
                "WHERE ABS(lat - $1) < 0.0005 AND ABS(lon - $2) < 0.0005 LIMIT 1",
                s["lat"], s["lon"],
                one=True,
            )

        if existing:
            # Обновляем только если поля пустые
            updates = []
            params = []
            idx = 1
            for field in ("name", "operator", "city", "address"):
                if not existing.get(field) and s.get(field):
                    if db.USE_SQLITE:
                        updates.append(f"{field} = ?")
                    else:
                        updates.append(f"{field} = ${idx}")
                        params.append(s[field])
                    idx += 1
            if updates:
                if db.USE_SQLITE:
                    updates.append("updated_at = datetime('now')")
                else:
                    updates.append(f"updated_at = NOW()")
                if db.USE_SQLITE:
                    sql = f"UPDATE stations SET {', '.join(updates)} WHERE id = ?"
                    params.append(existing["id"])
                else:
                    params.append(existing["id"])
                    sql = f"UPDATE stations SET {', '.join(updates)} WHERE id = ${idx}"
                await _execute(sql, *params)
                updated += 1
        else:
            # Создаём новую
            if db.USE_SQLITE:
                await _execute(
                    "INSERT INTO stations (name, operator, city, region, address, lat, lon, is_active, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, datetime('now'))",
                    s["name"], s.get("operator", ""), s.get("city", ""),
                    REGION_NAME, s.get("address", ""),
                    s["lat"], s["lon"],
                )
            else:
                await _execute(
                    "INSERT INTO stations (name, operator, city, region, address, lat, lon, is_active, created_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE, NOW())",
                    s["name"], s.get("operator", ""), s.get("city", ""),
                    REGION_NAME, s.get("address", ""),
                    s["lat"], s["lon"],
                )
            added += 1

    return len(stations), added, updated


async def main() -> dict:
    logger.info(f"=== Импорт АЗС {REGION_NAME} из OpenStreetMap ===")
    logger.info(f"BBox: {BBOX}")

    if not os.getenv("_API_MODE"):
        await db.init_db()

    logger.info("Запрашиваю Overpass API...")
    try:
        data = fetch_overpass()
    except Exception as e:
        logger.error(f"❌ Ошибка Overpass: {e}")
        if not os.getenv("_API_MODE"):
            await db.close_db()
        return {"ok": False, "error": str(e), "added": 0, "updated": 0}

    elements = data.get("elements", [])
    logger.info(f"Получено элементов: {len(elements)}")

    stations = []
    for e in elements:
        s = osm_tags_to_station(e)
        if s:
            stations.append(s)

    logger.info(f"Валидных станций: {len(stations)}")

    if not stations:
        logger.info("Нет станций для импорта")
        if not os.getenv("_API_MODE"):
            await db.close_db()
        return {"ok": True, "added": 0, "updated": 0, "total": 0}

    total, added, updated = await import_to_db(stations)
    logger.info(f"=== OSM result: total={total}, added={added}, updated={updated} ===")

    if not os.getenv("_API_MODE"):
        await db.close_db()

    return {"ok": True, "total": total, "added": added, "updated": updated}


if __name__ == "__main__":
    asyncio.run(main())
