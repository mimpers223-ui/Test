"""
Парсер цен из Telegram-каналов.

Получить API credentials (бесплатно):
  1. https://my.telegram.org/apps
  2. Создать приложение → api_id и api_hash
  3. Добавить в .env: TG_API_ID=12345 TG_API_HASH=abc...

Каналы (добавь свои):
  - @benzin_price
  - @azsprice
  - @toplivo_online
  - @benzinru
  - @fuel_monitoring
  - @gas_station_prices

Использование:
  export TG_API_ID='...'
  export TG_API_HASH='...'
  python scripts/parse_tg_prices.py --channel @benzin_price --limit 50
  python scripts/parse_tg_prices.py --all --limit 30

Парсит сообщения формата:
  "Лукойл, ул. Ленина 5: 92 - 54.40, 95 - 58.90, дизель - 67.20"
  или просто: "АИ-95 в Москве 56.40₽"
"""
import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

# Конфигурация каналов (добавь свои)
DEFAULT_CHANNELS = [
    "@benzin_price",
    "@azsprice",
    "@toplivo_online",
    "@benzinru",
    "@fuel_monitoring",
    "@gas_station_prices",
    "@prices_toplivo",
    "@azs_official",
]

# Ключевые слова для типов топлива
FUEL_PATTERNS = {
    "92": [r"\b(аи-?92|92)\b", r"а92", r"92й"],
    "95": [r"\b(аи-?95|95)\b", r"а95", r"95й", r"95\s"],
    "98": [r"\b(аи-?98|98)\b", r"а98", r"98й"],
    "100": [r"\b(аи-?100|100)\b"],
    "diesel": [r"\b(диз|дт|дизель)\b", r"diesel"],
    "lpg": [r"\b(газ|пропан|лpg)\b", r"пропан"],
}

# Паттерн цены
PRICE_RE = re.compile(r"(\d{2,3}[.,]\d{2})")


def parse_message_text(text: str) -> list[tuple[str, float]]:
    """Извлекает (fuel_type, price) из текста сообщения.

    Возвращает список найденных пар.
    """
    results = []
    # Сплит по строкам/запятым/точкам с запятой
    parts = re.split(r"[\n;]+|(?:\.\s+)|(?<=\d),", text)
    for part in parts:
        # Пытаемся найти тип топлива
        fuel = None
        for ftype, patterns in FUEL_PATTERNS.items():
            for p in patterns:
                if re.search(p, part, re.IGNORECASE):
                    fuel = ftype
                    break
            if fuel:
                break
        if not fuel:
            continue
        # Цена в этой части
        m = PRICE_RE.search(part)
        if not m:
            continue
        try:
            price = float(m.group(1).replace(",", "."))
            if 20 < price < 200:  # разумные цены
                results.append((fuel, price))
        except ValueError:
            continue
    return results


async def fetch_channel_messages(client, channel: str, limit: int = 50) -> list[Any]:
    """Читает последние N сообщений канала."""
    try:
        from telethon.tl.functions.channels import GetFullChannelRequest
        entity = await client.get_entity(channel)
        if hasattr(entity, "full_chat") is False:
            await client(GetFullChannelRequest(entity))
        messages = await client.get_messages(entity, limit=limit)
        return messages
    except Exception as e:
        print(f"  ❌ Не удалось получить {channel}: {e}")
        return []


async def save_price(station_name: str, fuel: str, price: float,
                    source_msg: str, source_url: str = "") -> bool:
    """Сохраняет цену в БД (через stations + reports)."""
    # TODO: нужен геокодинг для station_name → lat/lon
    # Пока сохраняем только имя, цена привяжется к station через geocoding
    return False


async def main():
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    tg_session_string = os.environ.get("TG_SESSION_STRING", "")
    if not api_id or not api_hash:
        print("❌ TG_API_ID и TG_API_HASH не заданы")
        print("Получить: https://my.telegram.org/apps")
        print("export TG_API_ID=12345")
        print("export TG_API_HASH=abc...")
        return 1

    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", help="Один канал (например @benzin_price)")
    parser.add_argument("--all", action="store_true", help="Все каналы из DEFAULT_CHANNELS")
    parser.add_argument("--limit", type=int, default=50, help="Макс сообщений на канал")
    args = parser.parse_args()

    channels = []
    if args.all:
        channels = DEFAULT_CHANNELS
    elif args.channel:
        channels = [args.channel]
    else:
        parser.print_help()
        return 1

    print(f"=== Telegram парсер цен ===")
    print(f"Каналы: {len(channels)}")
    print(f"Лимит: {args.limit} сообщений/канал")
    print()

    # Импорт Telethon (требует pip install telethon)
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("❌ pip install telethon")
        return 1

    await db.init_db()
    total_prices = 0
    total_msgs = 0

    if tg_session_string:
        client_ctx = TelegramClient(StringSession(tg_session_string), int(api_id), api_hash)
    else:
        client_ctx = TelegramClient("tg_session", int(api_id), api_hash)

    async with client_ctx as client:
        for channel in channels:
            print(f"[{channel}]")
            messages = await fetch_channel_messages(client, channel, args.limit)
            total_msgs += len(messages)
            for msg in messages:
                if not msg.text:
                    continue
                prices = parse_message_text(msg.text)
                if not prices:
                    continue
                print(f"  [{msg.date:%Y-%m-%d %H:%M}] {prices}")
                # Здесь будет сохранение в БД (после geocoding)
                total_prices += len(prices)

    print()
    print(f"=== Итого ===")
    print(f"  Сообщений: {total_msgs}")
    print(f"  Цен найдено: {total_prices}")
    await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
