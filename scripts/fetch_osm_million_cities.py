#!/usr/bin/env python3
"""
Пред-загрузка АЗС городов-миллионников РФ из OpenStreetMap.

⚠️ Запускать ЛОКАЛЬНО (Overpass API заблокирован с Render).
⚠️ После загрузки — закоммитить data/osm_million_cities.json в git
   и вызвать /api/import-osm?key=benzin-parse&region=Миллионники

Города-миллионники РФ (16 по данным Росстата на 01.01.2024):
  1. Москва               ~13.1M
  2. Санкт-Петербург      ~5.6M
  3. Новосибирск          ~1.6M
  4. Екатеринбург         ~1.5M
  5. Казань               ~1.3M
  6. Нижний Новгород      ~1.25M
  7. Челябинск            ~1.2M
  8. Самара               ~1.15M
  9. Омск                 ~1.1M
  10. Ростов-на-Дону      ~1.1M
  11. Уфа                 ~1.1M
  12. Красноярск          ~1.1M
  13. Воронеж             ~1.05M
  14. Волгоград           ~1.0M
  15. Пермь               ~1.0M
  16. Краснодар           ~1.0M  (опц., формально около 1M)
"""
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("osm_million")

OUT_FILE = Path(__file__).parent.parent / "data" / "osm_million_cities.json"

# === Города для импорта (lat, lon, name, OSM area id или bbox_radius_deg) ===
# bbox ~ 0.05° ≈ 5 км вокруг центра
CITIES = [
    # (name, lat, lon, radius_deg)
    ("Москва",              55.7558, 37.6173, 0.20),
    ("Санкт-Петербург",     59.9311, 30.3609, 0.18),
    ("Новосибирск",         55.0084, 82.9357, 0.15),
    ("Екатеринбург",        56.8389, 60.6057, 0.15),
    ("Казань",              55.8304, 49.0661, 0.13),
    ("Нижний Новгород",     56.3268, 44.0059, 0.13),
    ("Челябинск",           55.1600, 61.4000, 0.13),
    ("Самара",              53.1959, 50.1002, 0.13),
    ("Омск",                54.9885, 73.3242, 0.12),
    ("Ростов-на-Дону",      47.2225, 39.7187, 0.12),
    ("Уфа",                 54.7388, 55.9721, 0.12),
    ("Красноярск",          56.0100, 92.8525, 0.13),
    ("Воронеж",             51.6607, 39.2003, 0.10),
    ("Волгоград",           48.7194, 44.5018, 0.13),
    ("Пермь",               58.0105, 56.2502, 0.12),
    ("Краснодар",           45.0355, 38.9753, 0.12),
]

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]


def build_query(city: str, lat: float, lon: float, radius: float) -> str:
    """Overpass QL: все АЗС в радиусе вокруг центра города."""
    bbox = f"{lat - radius},{lon - radius},{lat + radius},{lon + radius}"
    return f"""
[out:json][timeout:90];
(
  node["amenity"="fuel"]({bbox});
  way["amenity"="fuel"]({bbox});
  relation["amenity"="fuel"]({bbox});
);
out center tags;
""".strip()


async def fetch_city(session, city: str, lat: float, lon: float, radius: float) -> list:
    query = build_query(city, lat, lon, radius)
    for i, url in enumerate(OVERPASS_URLS):
        try:
            logger.info("[%s] запрос %d/%d...", city, i + 1, len(OVERPASS_URLS))
            async with session.post(
                url,
                data={"data": query},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    elems = data.get("elements", [])
                    logger.info("[%s] ✅ %d элементов", city, len(elems))
                    return elems
                elif resp.status == 429:
                    logger.warning("[%s] rate limited, ждём 30с...", city)
                    await asyncio.sleep(30)
                    continue
                else:
                    text = await resp.text()
                    logger.warning("[%s] HTTP %d: %s", city, resp.status, text[:200])
                    await asyncio.sleep(5)
                    continue
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning("[%s] ошибка: %s", city, e)
            await asyncio.sleep(5)
            continue
    logger.error("[%s] ❌ все серверы недоступны", city)
    return []


async def main():
    all_elements = []
    city_counts = {}

    async with aiohttp.ClientSession() as session:
        for city, lat, lon, radius in CITIES:
            elems = await fetch_city(session, city, lat, lon, radius)
            # Помечаем каждый элемент городом
            for e in elems:
                tags = e.get("tags", {}) or {}
                if not tags.get("addr:city"):
                    tags["addr:city"] = city
                e["tags"] = tags
            all_elements.extend(elems)
            city_counts[city] = len(elems)
            logger.info("--- %s: %d, итого: %d ---", city, len(elems), len(all_elements))
            # Пауза чтобы не забанили
            await asyncio.sleep(2)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cities": [c[0] for c in CITIES],
        "counts": city_counts,
        "total": len(all_elements),
        "elements": all_elements,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("✅ Сохранено %d элементов в %s", len(all_elements), OUT_FILE)

    # Статистика
    logger.info("=== Итоги по городам ===")
    for city, cnt in city_counts.items():
        logger.info("  %-25s: %d", city, cnt)
    logger.info("  %-25s: %d", "ВСЕГО", sum(city_counts.values()))


if __name__ == "__main__":
    asyncio.run(main())
