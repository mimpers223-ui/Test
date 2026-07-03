"""
Парсер user-facing Telegram-бота @benzin_status_bot (Бензин Статус / Где Бензин?).

Бот 16 999+ MAU. Описание: "Где есть бензин прямо сейчас — карта от водителей для водителей."

Стратегия:
  1. Отправляем /start — получаем welcome
  2. Отправляем название города (например, "Москва")
  3. Читаем ответ бота — список АЗС с наличием топлива
  4. Парсим каждое сообщение → сохраняем в БД

⚠️  Требования:
  - TG_API_ID / TG_API_HASH / TG_SESSION_STRING (см. parse_tg_channels.py)
  - TG-аккаунт должен быть НЕ забанен ботом

Использование:
  python scripts/parse_benzin_status_bot.py
  python scripts/parse_benzin_status_bot.py --cities "Москва,Санкт-Петербург,Казань"
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent.parent / "bot" / ".env"
load_dotenv(ENV_PATH)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

TG_API_ID = os.getenv("TG_API_ID", "")
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION_STRING = os.getenv("TG_SESSION_STRING", "")
SESSION_PATH = Path(__file__).parent / "session"

BOT_USERNAME = "benzin_status_bot"

# Города-миллионники + крупные по умолчанию
DEFAULT_CITIES = [
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
    "Нижний Новгород", "Челябинск", "Самара", "Омск", "Ростов-на-Дону",
    "Уфа", "Красноярск", "Воронеж", "Волгоград", "Пермь",
    "Иваново", "Краснодар", "Саратов", "Тюмень", "Тольятти",
]

# === Ключевые слова для парсинга ответов бота ===
FUEL_KEYWORDS = {
    "92":     ["92", "аи-92", "аи92", "а92"],
    "95":     ["95", "аи-95", "аи95", "а95"],
    "98":     ["98", "аи-98", "аи98"],
    "100":    ["100", "аи-100", "аи100"],
    "diesel": ["дизель", "диз", "дт", "солярка"],
    "lpg":    ["газ", "пропан", "lpg"],
}

YES_WORDS = ["есть", "в наличии", "доступно", "наливают", "работает", "горит", "льют"]
NO_WORDS = ["нет", "отсутствует", "пусто", "закончился", "нету"]
LOW_WORDS = ["мало", "заканчивается", "осталось мало", "на исходе"]

NETWORK_KEYWORDS = {
    "Лукойл":         ["лукойл", "lukoil"],
    "Газпромнефть":   ["газпромнефть", "газпром", "gazprom"],
    "Роснефть":       ["роснефть", "rosneft"],
    "Татнефть":       ["татнефть", "tatneft"],
    "Башнефть":       ["башнефть", "bashneft"],
    "Shell":          ["шелл", "shell"],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levellevelname)s] %(message)s" if False else "%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("benzin_status_bot")
# fix typo above
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def parse_station_line(text: str) -> list[dict]:
    """Парсит строку ответа бота вида:
       'Лукойл, Минская 1: АИ-92 есть, АИ-95 нет'
       'Роснефть, ул. Ленина 5 — 92 ✅ 95 ❌ дизель ✅'
    Возвращает [{fuel_type, available, network, address, raw}, ...]
    """
    text_lower = text.lower()

    # Найти сеть
    network = None
    for net, kws in NETWORK_KEYWORDS.items():
        if any(kw in text_lower for kw in kws):
            network = net
            break

    # Найти адрес (после запятой, до двоеточия или тире)
    address = None
    m_addr = re.search(r"[,–—\-]\s*([^,:–—\-]+(?:,\s*\d+)?)\s*[—:\-–]", text)
    if m_addr:
        address = m_addr.group(1).strip()

    # Найти упоминания топлива
    found = []
    for fuel, fuel_kws in FUEL_KEYWORDS.items():
        for kw in fuel_kws:
            idx = text_lower.find(kw)
            if idx == -1:
                continue
            # Контекст ±15 символов вокруг ключевого слова
            ctx_start = max(0, idx - 15)
            ctx_end = min(len(text), idx + len(kw) + 15)
            ctx = text_lower[ctx_start:ctx_end]
            has_yes = any(w in ctx for w in YES_WORDS) or "✅" in ctx or "🟢" in ctx
            has_no = any(w in ctx for w in NO_WORDS) or "❌" in ctx or "🔴" in ctx
            has_low = any(w in ctx for w in LOW_WORDS) or "🟡" in ctx
            if has_yes:
                available = True
            elif has_no:
                available = False
            elif has_low:
                available = None
            else:
                available = None
            found.append({
                "fuel_type": fuel,
                "available": available,
                "network": network,
                "address": address,
                "raw": text,
            })
            break
    return found


async def find_station(network: Optional[str], address: Optional[str], city: str) -> Optional[int]:
    """Ищет АЗС в БД по сети + городу + (опционально) адресу."""
    if db.USE_SQLITE:
        if network and address:
            rows = await db._fetch(
                """SELECT id FROM stations
                   WHERE (LOWER(operator) LIKE ? OR LOWER(name) LIKE ?)
                     AND py_lower(city) = py_lower(?)
                     AND LOWER(address) LIKE ?
                   ORDER BY is_verified DESC, id LIMIT 1""",
                f"%{network.lower()}%",
                f"%{network.lower()}%",
                city,
                f"%{address.lower()[:30]}%",
            )
        elif network:
            rows = await db._fetch(
                """SELECT id FROM stations
                   WHERE (LOWER(operator) LIKE ? OR LOWER(name) LIKE ?)
                     AND py_lower(city) = py_lower(?)
                   ORDER BY is_verified DESC, id LIMIT 1""",
                f"%{network.lower()}%",
                f"%{network.lower()}%",
                city,
            )
        else:
            rows = await db._fetch(
                """SELECT id FROM stations
                   WHERE py_lower(city) = py_lower(?)
                   ORDER BY is_verified DESC, id LIMIT 1""",
                city,
            )
    else:
        async with db._db.acquire() as conn:
            if network and address:
                rows = await conn.fetch(
                    """SELECT id FROM stations
                       WHERE (LOWER(operator) LIKE $1 OR LOWER(name) LIKE $1)
                         AND LOWER(city) = LOWER($2)
                         AND LOWER(address) LIKE $3
                       ORDER BY is_verified DESC, id LIMIT 1""",
                    f"%{network.lower()}%",
                    city,
                    f"%{address.lower()[:30]}%",
                )
            elif network:
                rows = await conn.fetch(
                    """SELECT id FROM stations
                       WHERE (LOWER(operator) LIKE $1 OR LOWER(name) LIKE $1)
                         AND LOWER(city) = LOWER($2)
                       ORDER BY is_verified DESC, id LIMIT 1""",
                    f"%{network.lower()}%",
                    city,
                )
            else:
                rows = await conn.fetch(
                    """SELECT id FROM stations
                       WHERE LOWER(city) = LOWER($1)
                       ORDER BY is_verified DESC, id LIMIT 1""",
                    city,
                )
    if rows:
        return rows[0]["id"] if isinstance(rows[0], dict) else rows[0][0]
    return None


async def query_city(client, bot_entity, city: str) -> int:
    """Спрашивает у бота данные по городу, парсит ответ, сохраняет в БД.

    Возвращает количество сохранённых отчётов.
    """
    saved = 0
    try:
        # Отправляем название города
        await client.send_message(bot_entity, city)
        # Ждём 7 секунд, чтобы бот ответил
        await asyncio.sleep(7)
    except Exception as e:
        logger.warning("Ошибка отправки в бот: %s", e)
        return 0

    # Читаем последние ответы бота
    from telethon import functions
    try:
        result = await client(functions.messages.GetHistoryRequest(
            peer=bot_entity,
            limit=10,
            offset_date=None,
            offset_id=0,
            max_id=0,
            min_id=0,
            add_offset=0,
            hash=0,
        ))
        messages = result.messages
    except Exception as e:
        logger.warning("Не удалось прочитать историю: %s", e)
        return 0

    # DEBUG: показываем все сообщения с обеих сторон
    for m in messages:
        direction = "→" if m.outgoing else "←"
        text_preview = (m.message or "")[:200].replace("\n", " | ")
        logger.info("  %s %s: %s", direction, "me" if m.outgoing else "bot", text_preview)

    for msg in messages:
        if not msg.message or len(msg.message) < 5:
            continue
        if msg.outgoing:  # пропускаем свои сообщения
            continue
        # Парсим каждую строку сообщения
        for line in msg.message.split("\n"):
            if len(line.strip()) < 5:
                continue
            parsed = parse_station_line(line)
            for p in parsed:
                station_id = await find_station(p.get("network"), p.get("address"), city)
                if not station_id:
                    logger.debug("Skip: net=%s addr=%s city=%s (station not found)", p.get("network"), p.get("address"), city)
                    continue
                await db.add_report(
                    station_id=station_id,
                    fuel_type=p["fuel_type"],
                    available=p["available"],
                    source="benzin_status_bot",
                    comment=f"bot: {line.strip()[:200]}",
                )
                saved += 1
                logger.info("  ✅ station=%d fuel=%s avail=%s", station_id, p["fuel_type"], p["available"])
    return saved


async def run(cities: list[str]):
    if not TG_API_ID or not TG_API_HASH:
        logger.error("TG_API_ID / TG_API_HASH не заданы.")
        sys.exit(1)
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if TG_SESSION_STRING:
        client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
    else:
        client = TelegramClient(str(SESSION_PATH), int(TG_API_ID), TG_API_HASH)
    await client.start()
    me = await client.get_me()
    logger.info("Authorized as @%s", me.username)

    if not os.getenv("_API_MODE"):
        await db.init_db()
    await db.stale_old_reports("benzin_status_bot")

    # Найти бота
    try:
        bot_entity = await client.get_entity(BOT_USERNAME)
    except Exception as e:
        logger.error("Не удалось найти бота @%s: %s", BOT_USERNAME, e)
        await client.disconnect()
        return 0

    # Отправляем /start один раз
    try:
        await client.send_message(bot_entity, "/start")
        await asyncio.sleep(3)
    except Exception as e:
        logger.warning("Не удалось отправить /start: %s", e)

    total = 0
    for city in cities:
        try:
            count = await query_city(client, bot_entity, city)
            logger.info("✅ %s: %d отчётов", city, count)
            total += count
        except Exception as e:
            logger.warning("Ошибка для %s: %s", city, e)
        # Пауза между городами чтобы не забанили
        await asyncio.sleep(3)

    await client.disconnect()
    if not os.getenv("_API_MODE"):
        await db.close_db()
    logger.info("=== Total bot reports saved: %d ===", total)
    return total


def main():
    parser = argparse.ArgumentParser(description="Парсер @benzin_status_bot")
    parser.add_argument("--cities", default=",".join(DEFAULT_CITIES),
                        help=f"Города через запятую (default: {len(DEFAULT_CITIES)} городов)")
    args = parser.parse_args()
    cities = [c.strip() for c in args.cities.split(",") if c.strip()]
    asyncio.run(run(cities))


if __name__ == "__main__":
    main()
