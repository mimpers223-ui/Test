#!/usr/bin/env python3
"""
Импорт ВСЕХ АЗС Ивановской области из OpenStreetMap.
Данные заранее сохранены в data/osm_ivanovo.json (нельзя вызвать Overpass из Render — network blocked).

Идемпотентно — повторный запуск не дублирует.
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "bot", ".env"))

import db
from db import _fetch, _execute

# === Конфиг ===
REGION_NAME = "Ивановская область"
DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "osm_ivanovo.json")


def load_osm_data() -> list:
    """Загружает OSM данные из файла."""
    if not os.path.exists(DATA_FILE):
        logger.error(f"Файл {DATA_FILE} не найден!")
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("elements", [])


def osm_tags_to_station(elem: dict) -> dict:
    """Превращает OSM-элемент в нашу модель станции."""
    tags = elem.get("tags", {})
    center = elem.get("center", {})
    lat = elem.get("lat") or center.get("lat")

    lon = elem.get("lon") or center.get("lon")

    if not lat or not lon:
        return None

    name = tags.get("name") or tags.get("brand") or "АЗС"
    operator = tags.get("operator") or tags.get("brand")

    addr = tags.get("addr:street", "")
    if tags.get("addr:housenumber"):
        addr += f", {tags['addr:housenumber']}"
    if not addr:
        addr = tags.get("addr:full", "")

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
    added = 0
    updated = 0

    for s in stations:
        if not s.get("lat") or not s.get("lon"):
            continue

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
    logger.info(f"=== Импорт АЗС {REGION_NAME} из OSM (из файла) ===")

    if not db.API_MODE:
        await db.init_db()

    elements = load_osm_data()
    logger.info(f"Загружено элементов из файла: {len(elements)}")

    stations = []
    for e in elements:
        s = osm_tags_to_station(e)
        if s:
            stations.append(s)

    logger.info(f"Валидных станций: {len(stations)}")

    if not stations:
        logger.warning("Нет станций для импорта")
        if not db.API_MODE:
            await db.close_db()
        return {"ok": True, "added": 0, "updated": 0, "total": 0}

    # Показываем разбивку по операторам
    from collections import Counter
    ops = Counter()
    for s in stations:
        op = s.get("operator") or s["name"]
        ops[op] += 1
    logger.info("Топ операторов:")
    for op, cnt in ops.most_common(10):
        logger.info(f"  {op}: {cnt}")

    total, added, updated = await import_to_db(stations)
    logger.info(f"=== OSM result: total={total}, added={added}, updated={updated} ===")

    if not db.API_MODE:
        await db.close_db()

    return {"ok": True, "total": total, "added": added, "updated": updated}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(main())
