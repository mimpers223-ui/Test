"""
Парсер Telegram-каналов: вытаскивает упоминания о наличии топлива
и записывает их в БД как отчёты с source='telegram'.

⚠️  ВАЖНО ПЕРЕД ЗАПУСКОМ:
1. Зарегистрируй приложение на https://my.telegram.org/apps
2. Получи api_id и api_hash
3. Положи их в .env: TG_API_ID=... TG_API_HASH=...
4. При первом запуске попросит ввести телефон и SMS-код
5. После авторизации создаётся файл session.session — НЕ коммить его

Использование:
    python parse_tg_channels.py            # один проход
    python parse_tg_channels.py --watch    # слушать новые сообщения

⚖️  Юридически: читай только публичные каналы. Не пости от их имени.
    Сохраняй анонимно (без user_id, только текст).
"""
import argparse
import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import aiosqlite
import asyncpg
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Message

# Загружаем .env из backend/
ENV_PATH = Path(__file__).parent.parent / "backend" / ".env"
load_dotenv(ENV_PATH)

USE_SQLITE = os.getenv("USE_SQLITE", "true").lower() == "true"
DB_PATH = Path(__file__).parent.parent / "bot" / "benzin.db"
DATABASE_URL = os.getenv("DATABASE_URL", "")

TG_API_ID = os.getenv("TG_API_ID", "")
TG_API_HASH = os.getenv("TG_API_HASH", "")
SESSION_PATH = Path(__file__).parent / "session"

# Каналы для мониторинга (без @). Добавь свои.
CHANNELS = [
    "autobase37",        # пример: автоканал Иваново
    "moscow_auto",
    "spb_avto",
    "drive_2_chat",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("tg_parser")


# === Извлечение статуса из текста ===
FUEL_KEYWORDS = {
    "92": ["92", "аи-92", "аи92", "а92", "девяносто два"],
    "95": ["95", "аи-95", "аи95", "а95", "девяносто пять"],
    "98": ["98", "аи-98", "аи98"],
    "diesel": ["дизель", "диз", "солярка", "соляра"],
    "lpg": ["газ", "пропан", "lpg"],
}

NETWORK_KEYWORDS = {
    "lukoil": ["лукойл", "lukoil"],
    "gazprom": ["газпром", "газпромнефть", "gazprom"],
    "rosneft": ["роснефть", "rosneft"],
    "tatneft": ["татнефть", "tatneft"],
    "bashneft": ["башнефть", "bashneft"],
    "teboil": ["teboil", "тебойл"],
    "shell": ["shell", "шелл"],
}

YES_WORDS = ["есть", "завезли", "привезли", "появилось", "в наличии", "льют", "работает"]
NO_WORDS = ["нет", "отсутствует", "пусто", "закончился", "кончился", "закончилось", "кончилось", "нету"]
LOW_WORDS = ["мало", "заканчивается", "кончается", "осталось мало", "на исходе"]


def parse_fuel_status(text: str) -> list[dict]:
    """Извлекает упоминания топлива и их статус из текста поста.

    Возвращает [{fuel_type, available, network}, ...]
    """
    text_lower = text.lower()
    results = []

    # Найти сеть
    network = None
    for net, kws in NETWORK_KEYWORDS.items():
        if any(kw in text_lower for kw in kws):
            network = net
            break

    # Найти виды топлива и статусы
    # Берём контекст: 50 символов вокруг ключевого слова топлива
    for fuel, fuel_kws in FUEL_KEYWORDS.items():
        for kw in fuel_kws:
            idx = text_lower.find(kw)
            if idx == -1:
                continue
            # Контекст ±60 символов
            ctx_start = max(0, idx - 60)
            ctx_end = min(len(text), idx + len(kw) + 60)
            ctx = text_lower[ctx_start:ctx_end]

            available = None
            if any(w in ctx for w in YES_WORDS):
                available = True
            elif any(w in ctx for w in NO_WORDS):
                available = False
            elif any(w in ctx for w in LOW_WORDS):
                available = None  # "кончается"
            else:
                continue  # не нашли статуса — пропускаем

            results.append({"fuel_type": fuel, "available": available, "network": network})
            break  # один результат на вид топлива

    return results


# === Поиск АЗС по тексту ===
async def find_station_by_text(network: str | None, lat: float | None = None, lon: float | None = None):
    """Ищет АЗС в БД: сначала по network, потом по гео."""
    if not USE_SQLITE:
        return None  # TODO: PostgreSQL
    conn = await aiosqlite.connect(str(DB_PATH))
    conn.row_factory = aiosqlite.Row
    try:
        if network:
            async with conn.execute(
                """SELECT id, name, operator, lat, lon, city
                   FROM stations
                   WHERE LOWER(operator) LIKE ? OR LOWER(name) LIKE ?
                   LIMIT 5""",
                (f"%{network}%", f"%{network}%"),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
            if rows:
                return rows
        if lat and lon:
            async with conn.execute(
                """SELECT id, name, operator, lat, lon, city
                   FROM stations
                   WHERE ABS(lat - ?) < 0.1 AND ABS(lon - ?) < 0.1
                   LIMIT 5""",
                (lat, lon),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
            return rows
    finally:
        await conn.close()
    return []


async def save_telegram_report(station_id: int, fuel_type: str, available: bool | None, raw_text: str):
    """Сохраняет отчёт от парсера Telegram."""
    from datetime import datetime, timedelta
    expires_at = (datetime.now() + timedelta(hours=6)).isoformat()
    if not USE_SQLITE:
        logger.warning("PostgreSQL path not implemented, skipping")
        return
    conn = await aiosqlite.connect(str(DB_PATH))
    try:
        # SQLite: 1=True, 0=False, 2=None
        avail_int = 1 if available is True else (0 if available is False else 2)
        await conn.execute(
            """INSERT INTO reports (station_id, fuel_type, available, source, expires_at)
               VALUES (?, ?, ?, 'telegram', ?)""",
            (station_id, fuel_type, avail_int, expires_at),
        )
        await conn.commit()
        logger.info("Saved TG report: station=%d fuel=%s available=%s text=%r", station_id, fuel_type, available, raw_text[:60])
    finally:
        await conn.close()


async def handle_message(msg: Message):
    """Обрабатывает одно сообщение: парсит и сохраняет."""
    if not msg.text or len(msg.text) < 10:
        return
    parsed = parse_fuel_status(msg.text)
    if not parsed:
        return
    for p in parsed:
        stations = await find_station_by_text(p["network"])
        if not stations:
            logger.debug("No station found for network=%s text=%r", p["network"], msg.text[:80])
            continue
        # Берём первую найденную АЗС
        st = stations[0]
        await save_telegram_report(st["id"], p["fuel_type"], p["available"], msg.text)


async def run_once():
    """Один проход: читает последние N сообщений из каждого канала."""
    if not TG_API_ID or not TG_API_HASH:
        logger.error("TG_API_ID / TG_API_HASH не заданы. См. инструкции в начале файла.")
        sys.exit(1)
    client = TelegramClient(str(SESSION_PATH), int(TG_API_ID), TG_API_HASH)
    await client.start()
    logger.info("Authorized as %s", (await client.get_me()).username)

    for channel in CHANNELS:
        try:
            entity = await client.get_entity(channel)
        except Exception as e:
            logger.warning("Cannot find channel %s: %s", channel, e)
            continue
        logger.info("Scanning channel: %s", channel)
        count = 0
        async for msg in client.iter_messages(entity, limit=200):
            await handle_message(msg)
            count += 1
        logger.info("  Scanned %d messages", count)
    await client.disconnect()


async def run_watch():
    """Слушает новые сообщения в реальном времени."""
    if not TG_API_ID or not TG_API_HASH:
        logger.error("TG_API_ID / TG_API_HASH не заданы.")
        sys.exit(1)
    client = TelegramClient(str(SESSION_PATH), int(TG_API_ID), TG_API_HASH)
    await client.start()
    logger.info("Watching for new messages in: %s", CHANNELS)

    @client.on(events.NewMessage(chats=CHANNELS))
    async def handler(event):
        await handle_message(event.message)

    await client.run_until_disconnected()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="Слушать новые сообщения в реальном времени")
    args = parser.parse_args()
    if args.watch:
        asyncio.run(run_watch())
    else:
        asyncio.run(run_once())


if __name__ == "__main__":
    main()
