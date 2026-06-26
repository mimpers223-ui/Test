"""
Парсер fuelprice.ru — крупный агрегатор АЗС с ценами (60+ городов).

Источник: https://fuelprice.ru
- 60+ городов России
- Данные: координаты + название + цены (АИ-92, АИ-95, АИ-98, ДТ, ГАЗ)
- Формат: JSON в HTML (Яндекс.Карты API)
- Обновление: регулярное
- Confidence: 0.75 (крупный агрегатор с координатами)

Структура данных:
  [lat, lon, station_name, prices_html, ...]
  prices_html: "Аи-92: <strong>74.9</strong> руб. (2026-06-26)<br>Аи-95: <strong>81.9</strong> руб. (2026-06-26)<br>"
"""
import argparse
import asyncio
import os
import re
import sys
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

BASE_URL = "https://fuelprice.ru"
SOURCE_NAME = "fuelprice_ru"

# Маппинг: коды в БД ↔ названия в fuelprice
FUEL_MAP = {
    "Аи-92": "92",
    "АИ-92": "92",
    "92": "92",
    "Аи-95": "95",
    "АИ-95": "95",
    "95": "95",
    "Аи-98": "98",
    "АИ-98": "98",
    "98": "98",
    "ДТ": "diesel",
    "Дизель": "diesel",
    "дизель": "diesel",
    "Газ": "lpg",
    "Пропан": "lpg",
    "газ": "lpg",
    "пропан": "lpg",
}


def parse_cities(html: str) -> list[str]:
    """Извлекает список городов с главной."""
    soup = BeautifulSoup(html, "html.parser")
    cities = set()
    for a in soup.find_all("a", href=re.compile(r"^/[a-z][a-z-]*$")):
        href = a.get("href", "").lstrip("/")
        if href and not href.startswith(("news", "about", "contact", "map", "login", "register", "static", "media")):
            cities.add(href)
    return sorted(cities)


def parse_prices_from_html(html: str) -> dict[str, float]:
    """Извлекает цены из HTML-блока fuelprice.ru.

    Пример: "Аи-92: <strong>74.9</strong> руб. (2026-06-26)<br>"
    """
    prices = {}
    # Паттерн: "Аи-92:" или "АИ-95:" + <strong>ЦЕНА</strong>
    pattern = r"(Аи-?\d+|ДТ|дизель|газ|пропан)[:\s]*<strong>\s*(\d{2,3}[.,]\d{2})\s*</strong>"
    for m in re.finditer(pattern, html, re.IGNORECASE):
        fuel_name = m.group(1).strip()
        price_str = m.group(2).replace(",", ".")
        # Нормализуем ключ
        for k, v in FUEL_MAP.items():
            if fuel_name.lower() == k.lower():
                prices[v] = float(price_str)
                break
    return prices


def parse_stations_json(html: str) -> list[dict]:
    """Извлекает JSON-массив станций из HTML fuelprice.ru.

    Формат: [lat, lon, name, prices_html, ...]
    """
    # Ищем JSON-массив в HTML: [ ... ]
    soup = BeautifulSoup(html, "html.parser")

    # Часто JSON встроен в script или прямо в HTML
    # Ищем паттерн: [55.123, 37.456, 'Name', 'html...', ...]
    pattern = r"\[\s*(\d+\.\d+)\s*,\s*(\d+\.\d+)\s*,\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]"
    stations = []
    for m in re.finditer(pattern, html):
        try:
            lat = float(m.group(1))
            lon = float(m.group(2))
            name = m.group(3).strip()
            prices_html = m.group(4)

            prices = parse_prices_from_html(prices_html)
            if name and prices:
                stations.append({
                    "lat": lat,
                    "lon": lon,
                    "name": name,
                    "prices": prices,
                })
        except (ValueError, IndexError):
            continue
    return stations


async def fetch(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=30),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
        ) as r:
            if r.status == 200:
                return await r.text()
            else:
                print(f"  ⚠ HTTP {r.status}: {url}")
    except Exception as e:
        print(f"  ⚠ {url}: {e}")
    return None


def normalize_name(name: str) -> str:
    """Нормализует название АЗС для матчинга."""
    # "АЗС №123 'Лукойл'" -> "Лукойл"
    name = re.sub(r"АЗС\s*[№#]?\s*\d+\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"['\"]", "", name)
    return name.strip()


async def find_matching_station(stations_cache: dict, lat: float, lon: float, name: str) -> Optional[int]:
    """Ищет существующую АЗС в БД по координатам (±0.005 = ±500м)."""
    key = (round(lat, 3), round(lon, 3))
    if key in stations_cache:
        return stations_cache[key]
    try:
        row = await db._fetch(
            "SELECT id FROM stations WHERE ABS(lat - $1) < 0.005 AND ABS(lon - $2) < 0.005 LIMIT 1",
            lat, lon, one=True
        )
        if row:
            stations_cache[key] = row["id"]
            return row["id"]
    except Exception:
        pass
    return None


async def create_station(lat: float, lon: float, name: str) -> Optional[int]:
    """Создаёт новую АЗС в БД (если совпадений не найдено)."""
    try:
        # Пытаемся сначала найти
        existing = await db._fetch(
            "SELECT id FROM stations WHERE ABS(lat - $1) < 0.0001 AND ABS(lon - $2) < 0.0001 LIMIT 1",
            lat, lon, one=True
        )
        if existing:
            return existing["id"]

        result = await db._execute(
            """
            INSERT INTO stations (name, lat, lon, operator, is_active, created_at)
            VALUES ($1, $2, $3, $4, TRUE, NOW())
            RETURNING id
            """,
            name, lat, lon, name,
            returning=True
        )
        if result:
            return result[0]["id"] if isinstance(result, list) else result.get("id")
    except Exception as e:
        # Если дубль — ищем заново
        try:
            existing = await db._fetch(
                "SELECT id FROM stations WHERE ABS(lat - $1) < 0.0001 AND ABS(lon - $2) < 0.0001 LIMIT 1",
                lat, lon, one=True
            )
            if existing:
                return existing["id"]
        except Exception:
            pass
    return None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", help="Город (slug, например: moskva, spb)")
    parser.add_argument("--limit", type=int, default=0, help="Лимит станций на город (0 = без лимита)")
    parser.add_argument("--dry-run", action="store_true", help="Не сохранять в БД")
    parser.add_argument("--create-new", action="store_true", help="Создавать новые АЗС в БД (если не найдены)")
    args = parser.parse_args()

    print(f"=== Парсер fuelprice.ru ===")
    if not args.dry_run:
        await db.init_db()

    async with aiohttp.ClientSession() as session:
        cities = []
        if args.city:
            cities = [args.city]
            print(f"Город: {args.city}")
        else:
            print("Загружаю список городов...")
            html = await fetch(session, BASE_URL)
            if not html:
                print("❌ Не удалось загрузить главную")
                return 1
            cities = parse_cities(html)
            print(f"Найдено городов: {len(cities)}")

        total_stations = 0
        total_prices = 0
        total_matched = 0
        total_created = 0
        total_saved = 0
        stations_cache = {}

        for city_slug in cities:
            print(f"\n[{city_slug}]")
            url = f"{BASE_URL}/{city_slug}"
            html = await fetch(session, url)
            if not html:
                print(f"  ❌ Не удалось загрузить")
                continue

            stations = parse_stations_json(html)
            if args.limit > 0:
                stations = stations[:args.limit]

            print(f"  Найдено АЗС с ценами: {len(stations)}")
            total_stations += len(stations)

            for st in stations:
                prices_count = len(st["prices"])
                total_prices += prices_count

                if args.dry_run:
                    continue

                # Матчинг по координатам
                station_id = await find_matching_station(
                    stations_cache, st["lat"], st["lon"], st["name"]
                )

                if station_id is None:
                    if args.create_new:
                        station_id = await create_station(
                            st["lat"], st["lon"], st["name"]
                        )
                        if station_id:
                            total_created += 1
                    else:
                        # Пропускаем — нет матча
                        continue
                else:
                    total_matched += 1

                # Сохраняем цены по каждому fuel_type
                for fuel, price in st["prices"].items():
                    try:
                        await db.add_report(
                            station_id=station_id,
                            fuel_type=fuel,
                            available=True,
                            price=price,
                            source=SOURCE_NAME,
                            comment=f"fuelprice.ru: {st['name']}",
                        )
                        total_saved += 1
                    except Exception as e:
                        if total_saved < 3:
                            print(f"    ⚠ add_report {fuel}/{price}: {e}")

            await asyncio.sleep(0.5)  # Уважение к серверу

    print()
    print(f"=== Итого ===")
    print(f"  Городов: {len(cities)}")
    print(f"  АЗС с ценами: {total_stations}")
    print(f"  Цен найдено: {total_prices}")
    if not args.dry_run:
        print(f"  Матчей в БД: {total_matched}")
        print(f"  Новых АЗС создано: {total_created}")
        print(f"  Цен сохранено: {total_saved}")
        await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
