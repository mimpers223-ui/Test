#!/usr/bin/env python3
"""Парсер данных с benzinmap.ru — региональные ограничения на продажу топлива.

Загружает data.json с benzinmap.ru и сохраняет как source='benzinmap'.
Извлекает лимиты, запреты на канистры и статусы по 62 регионам.

Использование:
    python scripts/parse_benzinmap.py
"""

import asyncio
import sys
import os
import re
import logging
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "bot", ".env"))

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BENZINMAP_URL = "https://benzinmap.ru/data.json"

# Маппинг кодов регионов benzinmap.ru → названия в БД
REGION_CODE_MAP = {
    "RU-CR": "Республика Крым",
    "RU-SEV": "Севастополь",
    "RU-MOW": "Москва",
    "RU-MOS": "Московская область",
    "RU-SPE": "Санкт-Петербург",
    "RU-LEN": "Ленинградская область",
    "RU-BEL": "Белгородская область",
    "RU-KRS": "Курская область",
    "RU-BRY": "Брянская область",
    "RU-PSK": "Псковская область",
    "RU-KDA": "Краснодарский край",
    "RU-VOR": "Воронежская область",
    "RU-SAR": "Саратовская область",
    "RU-OMS": "Омская область",
    "RU-SE": "Северная Осетия — Алания",
    "RU-TA": "Татарстан",
    "RU-RYA": "Рязанская область",
    "RU-MUR": "Мурманская область",
    "RU-ARK": "Архангельская область",
    "RU-NGR": "Новгородская область",
    "RU-SAM": "Самарская область",
    "RU-NIZ": "Нижегородская область",
    "RU-PRI": "Приморский край",
    "RU-TY": "Республика Тыва",
    "RU-TUL": "Тульская область",
    "RU-TYU": "Тюменская область",
    "RU-ORE": "Оренбургская область",
    "RU-SVE": "Свердловская область",
    "RU-CHE": "Челябинская область",
    "RU-BA": "Республика Башкортостан",
    "RU-IRK": "Иркутская область",
    "RU-KEM": "Кемеровская область",
    "RU-NVS": "Новосибирская область",
    "RU-KIR": "Кировская область",
    "RU-KOS": "Костромская область",
    "RU-KYA": "Красноярский край",
    "RU-TOM": "Томская область",
    "RU-KHA": "Хабаровский край",
    "RU-SA": "Республика Саха (Якутия)",
    "RU-VLG": "Вологодская область",
    "RU-VGG": "Волгоградская область",
    "RU-KO": "Республика Коми",
    "RU-PNZ": "Пензенская область",
    "RU-ROS": "Ростовская область",
    "RU-SMO": "Смоленская область",
    "RU-KGN": "Курганская область",
    "RU-KHM": "Ханты-Мансийский автономный округ",
    "RU-YAN": "Ямало-Ненецкий автономный округ",
    "RU-AD": "Республика Адыгея",
    "RU-LIP": "Липецкая область",
    "RU-ULY": "Ульяновская область",
    "RU-VLA": "Владимирская область",
    "RU-TVE": "Тверская область",
    "RU-MO": "Республика Мордовия",
    "RU-DA": "Республика Дагестан",
    "RU-SAK": "Сахалинская область",
    "RU-TAM": "Тамбовская область",
    "RU-ZAB": "Забайкальский край",
    "RU-ORL": "Орловская область",
    "RU-BU": "Республика Бурятия",
    "RU-KGD": "Калининградская область",
    "RU-AL": "Республика Алтай",
}


def extract_limit_from_detail(detail: str) -> Optional[int]:
    """Извлекает лимит в литрах из текста описания."""
    if not detail:
        return None
    detail_lower = detail.lower()

    patterns = [
        r'(?:лимит|не более|до|максимум)\s*(\d{1,3})\s*(?:л(?:итр)?(?:\s|,|\.|$))',
        r'(\d{1,3})\s*(?:л(?:итр)?)\s*(?:в одни руки|на машину|на авто|на легков)',
        r'(\d{1,3})\s*(?:л(?:итр)?)\s*(?:бензин|дизел|топлив)',
        r'(\d{1,3})\s*(?:л(?:итр)?)\s*(?:в\s+бак)',
        r'не\s+более\s+(\d{1,3})\s*(?:л(?:итр)?)',
    ]

    for pattern in patterns:
        match = re.search(pattern, detail_lower)
        if match:
            num = int(match.group(1))
            if 5 <= num <= 500:
                return num
    return None


def extract_canister_ban(detail: str) -> bool:
    """Определяет запрет на заправку в канистры из текста описания."""
    if not detail:
        return False
    detail_lower = detail.lower()

    ban_phrases = [
        "канистр", "запрет заправки в канистр",
        "запрет налив", "запрещены канистр",
        "запрет на канистр", "тара запрещена",
    ]
    for phrase in ban_phrases:
        if phrase in detail_lower:
            return True

    if "канистр" in detail_lower and any(
        kw in detail_lower for kw in ["запрет", "запрещ", "не допуска", "не разреш"]
    ):
        return True

    return False


def get_region_search_names(code: str, name: str, aliases: list) -> list[str]:
    """Формирует список вариантов названия региона для поиска в БД."""
    names = []

    names.append(name)

    short = name.replace("область", "").replace("край", "").replace("республика", "")
    short = short.replace("автономный округ", "").strip()
    if short and short != name:
        names.append(short)

    if "республика" in name.lower():
        rep_short = name.lower().replace("республика", "").strip()
        if rep_short:
            names.append(rep_short.capitalize())

    if "республика" in name.lower():
        rep_short = name.lower().replace("республика", "").strip()
        if rep_short:
            names.append(rep_short)

    if "автономный округ" in name.lower():
        ao_short = name.lower().replace("автономный округ", "").strip()
        if ao_short:
            names.append(ao_short)
            names.append(ao_short.capitalize())

    if "АО" in name:
        ao_short = name.replace("АО", "").strip()
        if ao_short:
            names.append(ao_short)
            names.append(ao_short + " автономный округ")
            names.append(ao_short + " Автономный округ")

    names.extend(aliases)

    unique = []
    seen = set()
    for n in names:
        n_stripped = n.strip()
        if n_stripped and n_stripped.lower() not in seen:
            seen.add(n_stripped.lower())
            unique.append(n_stripped)
    return unique


async def fetch_data() -> Optional[dict]:
    """Загружает data.json с benzinmap.ru."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(BENZINMAP_URL, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                else:
                    logger.warning(f"HTTP {resp.status} from benzinmap.ru")
                    return None
    except Exception as e:
        logger.error(f"Failed to fetch benzinmap.ru data: {e}")
        return None


async def find_stations_for_region(search_names: list[str]) -> list[dict]:
    """Находит станции по региону (ищет по region и city)."""
    conditions = []
    params = []
    for name in search_names:
        conditions.append("region LIKE ?")
        params.append(f"%{name}%")

    for name in search_names:
        conditions.append("city LIKE ?")
        params.append(f"%{name}%")

    where = " OR ".join(conditions)
    query = f"SELECT id, name, city, region FROM stations WHERE {where} LIMIT 500"

    return await db._fetch(query, *params)


async def save_region_limits(data: dict) -> int:
    """Сохраняет лимиты по регионам в БД."""
    saved = 0
    updated = data.get("updated", "unknown")

    for region in data.get("regions", []):
        code = region.get("code", "")
        name = region.get("ru", "")
        status = region.get("status", "")
        detail = region.get("detail", "")
        aliases = region.get("aliases", [])

        if code in REGION_CODE_MAP:
            name = REGION_CODE_MAP[code]

        limit_liters = extract_limit_from_detail(detail)
        canister_ban = extract_canister_ban(detail)
        has_limit = status in ("stopped", "limit") or limit_liters is not None

        search_names = get_region_search_names(code, name, aliases)
        stations = await find_stations_for_region(search_names)

        if not stations:
            logger.warning(f"  Stations not found for region: {name} ({code})")
            continue

        for station in stations:
            comment_parts = []
            if status == "stopped":
                comment_parts.append("ПРОДАЖА ПРЕКРАЩЕНА")
            elif status == "limit":
                comment_parts.append("ЛИМИТЫ")
            elif status == "local":
                comment_parts.append("Локальные ограничения")
            if limit_liters:
                comment_parts.append(f"Лимит: {limit_liters} л")
            if canister_ban:
                comment_parts.append("ЗАПРЕТ НА КАНИСТРЫ")
            comment_parts.append(f"[benzinmap {updated}]")

            try:
                await db.add_report(
                    station_id=station["id"],
                    fuel_type="all",
                    available=None,
                    has_limit=has_limit,
                    limit_per_visit=limit_liters,
                    canister_ban=canister_ban,
                    comment=" | ".join(comment_parts)[:500],
                    source="benzinmap",
                )
                saved += 1
            except Exception as e:
                logger.warning(f"  Error saving report for station {station['id']}: {e}")

    logger.info(f"BenZinMap: saved {saved} reports (data from {updated})")
    return saved


async def main():
    await db.init_db()
    logger.info("Fetching benzinmap.ru data...")
    data = await fetch_data()
    if data:
        saved = await save_region_limits(data)
        logger.info(f"Done. Saved {saved} reports.")
    else:
        logger.error("Failed to fetch data from benzinmap.ru")
    await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
