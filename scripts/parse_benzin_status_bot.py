"""
Парсер user-facing Telegram-бота @benzin_status_bot (Бензин Статус / Где Бензин?).

Бот 16 999+ MAU. Описание: "Где есть бензин прямо сейчас — карта от водителей для водителей."

Стратегия:
  1. Открыть Mini App бота (Web App)
  2. Извлечь URL Mini App из кнопки бота
  3. Запросить данные через Mini App API (HTTP)
  4. Сохранить в БД

Mini App открывается через /start + кнопку в клавиатуре.
URL имеет вид https://something.telegram.org/...
Параметры передаются через ?tgWebAppStartParam=...

⚠️  Требования:
  - TG_API_ID / TG_API_HASH / TG_SESSION_STRING
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
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlencode

import aiohttp
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

NETWORK_KEYWORDS = {
    "Лукойл":         ["лукойл", "lukoil"],
    "Газпромнефть":   ["газпромнефть", "газпром", "gazprom"],
    "Роснефть":       ["роснефть", "rosneft"],
    "Татнефть":       ["татнефть", "tatneft"],
    "Башнефть":       ["башнефть", "bashneft"],
    "Shell":          ["шелл", "shell"],
}

FUEL_KEYWORDS = {
    "92":     ["92", "аи-92", "аи92", "а92"],
    "95":     ["95", "аи-95", "аи95", "а95"],
    "98":     ["98", "аи-98", "аи98"],
    "100":    ["100", "аи-100", "аи100"],
    "diesel": ["дизель", "диз", "дт", "солярка"],
    "lpg":    ["газ", "пропан", "lpg"],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("benzin_status_bot")


def extract_web_app_url(messages) -> Optional[str]:
    """Извлекает URL Mini App из кнопок бота (web_app type)."""
    for msg in messages:
        if not msg.reply_markup:
            continue
        try:
            rows = msg.reply_markup.rows if hasattr(msg.reply_markup, 'rows') else []
            for row in rows:
                for button in row.buttons:
                    # telethon Button object
                    if hasattr(button, 'url') and button.url:
                        return button.url
                    # InlineKeyboardButton.web_app
                    if hasattr(button, 'web_view') and button.web_view:
                        return getattr(button.web_view, 'url', None)
                    # Try to get from data
                    if hasattr(button, 'data') and button.data:
                        try:
                            d = json.loads(button.data.decode() if isinstance(button.data, bytes) else button.data)
                            if 'url' in d:
                                return d['url']
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
        except Exception as e:
            logger.debug("extract_web_app_url: %s", e)
    return None


async def find_station(network: Optional[str], address: Optional[str], city: str) -> Optional[int]:
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
                    f"%{network.lower()}%", city, f"%{address.lower()[:30]}%",
                )
            elif network:
                rows = await conn.fetch(
                    """SELECT id FROM stations
                       WHERE (LOWER(operator) LIKE $1 OR LOWER(name) LIKE $1)
                         AND LOWER(city) = LOWER($2)
                       ORDER BY is_verified DESC, id LIMIT 1""",
                    f"%{network.lower()}%", city,
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


def parse_station_text(text: str) -> list[dict]:
    """Парсит строку/сообщение бота → список станций с топливом."""
    text_lower = text.lower()
    network = None
    for net, kws in NETWORK_KEYWORDS.items():
        if any(kw in text_lower for kw in kws):
            network = net
            break
    address = None
    m_addr = re.search(r"[,–—\-]\s*([^,:–—\-]+(?:,\s*\d+)?)\s*[—:\-–]", text)
    if m_addr:
        address = m_addr.group(1).strip()

    found = []
    for fuel, fuel_kws in FUEL_KEYWORDS.items():
        for kw in fuel_kws:
            idx = text_lower.find(kw)
            if idx == -1:
                continue
            ctx = text_lower[max(0, idx - 15):min(len(text), idx + len(kw) + 15)]
            has_yes = any(w in ctx for w in ["есть", "в наличии", "горит", "✅", "🟢"])
            has_no = any(w in ctx for w in ["нет", "отсутствует", "пусто", "❌", "🔴"])
            has_low = any(w in ctx for w in ["мало", "заканчивается", "🟡"])
            if has_yes:
                available = True
            elif has_no:
                available = False
            elif has_low:
                available = None
            else:
                available = None
            found.append({"fuel_type": fuel, "available": available, "network": network, "address": address})
            break
    return found


async def fetch_mini_app_data(mini_app_url: str, city: str) -> list[dict]:
    """Парсит данные из Mini App (Web App).

    Mini App обычно это SPA, который загружает данные через API.
    Без знания точного API эндпоинта — пробуем common пути.
    """
    parsed = urlparse(mini_app_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    candidates = [
        f"{base}/api/stations?city={city}",
        f"{base}/api/v1/stations?city={city}",
        f"{base}/api/fuel?city={city}",
        f"{base}/api/gas?city={city}",
        f"{base}/api/data?city={city}",
    ]

    async with aiohttp.ClientSession() as session:
        for url in candidates:
            try:
                logger.info("  Пробую %s", url)
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15),
                                       headers={"User-Agent": "Mozilla/5.0"}) as resp:
                    if resp.status == 200:
                        ct = resp.headers.get("content-type", "")
                        if "json" in ct:
                            data = await resp.json()
                            return data if isinstance(data, list) else data.get("stations") or data.get("data") or []
            except Exception as e:
                logger.debug("  %s: %s", url, e)
    return []


async def query_mini_app(client, bot_entity, cities: list[str]) -> int:
    """Открывает Mini App бота, парсит данные по городам.

    Шаги:
      1. /start
      2. Найти кнопку web_app в клавиатуре
      3. Получить URL
      4. Запросить данные для каждого города
    """
    from telethon import functions

    # /start чтобы получить welcome + keyboard
    try:
        await client.send_message(bot_entity, "/start")
        await asyncio.sleep(5)
    except Exception as e:
        logger.warning("Не удалось отправить /start: %s", e)
        return 0

    result = await client(functions.messages.GetHistoryRequest(
        peer=bot_entity, limit=5, offset_date=None, offset_id=0,
        max_id=0, min_id=0, add_offset=0, hash=0,
    ))
    messages = result.messages

    # DEBUG
    for m in messages:
        direction = "→" if m.outgoing else "←"
        logger.info("  %s %s: %s | markup=%s",
                    direction, "me" if m.outgoing else "bot",
                    (m.message or "")[:200].replace("\n", " | "),
                    type(m.reply_markup).__name__ if m.reply_markup else "None")

    # Ищем URL Mini App в кнопках
    mini_app_url = extract_web_app_url(messages)
    if not mini_app_url:
        # Fallback: проверяем текст на URL
        for m in messages:
            if m.message:
                urls = re.findall(r'https?://[^\s\)]+', m.message)
                for u in urls:
                    if 'telegram' in u or 'miniapp' in u or 'app' in u:
                        mini_app_url = u
                        break
                if mini_app_url:
                    break
    if not mini_app_url:
        logger.warning("❌ Mini App URL не найден в кнопках и тексте бота")
        return 0
    logger.info("✅ Mini App URL: %s", mini_app_url)

    # Пробуем парсить данные
    saved = 0
    for city in cities:
        try:
            data = await fetch_mini_app_data(mini_app_url, city)
            if not data:
                logger.info("  [%s] пусто", city)
                continue
            logger.info("  [%s] получено %d записей", city, len(data))
            for item in data:
                if not isinstance(item, dict):
                    continue
                network = item.get("network") or item.get("operator") or item.get("brand")
                address = item.get("address") or item.get("street")
                station_id = await find_station(network, address, city)
                if not station_id:
                    continue
                # Ищем упоминания топлива
                text = json.dumps(item, ensure_ascii=False)
                parsed = parse_station_text(text)
                for p in parsed:
                    await db.add_report(
                        station_id=station_id,
                        fuel_type=p["fuel_type"],
                        available=p["available"],
                        source="benzin_status_bot",
                        comment=f"miniapp: {city} {address or ''}"[:200],
                    )
                    saved += 1
        except Exception as e:
            logger.warning("  [%s] ошибка: %s", city, e)
        await asyncio.sleep(2)
    return saved


async def run(cities: list[str]):
    logger.info("=== Запуск парсера @%s (Mini App) ===", BOT_USERNAME)
    logger.info("Городов: %d, API keys: id=%s hash=%s session=%s",
                len(cities),
                "✓" if TG_API_ID else "✗",
                "✓" if TG_API_HASH else "✗",
                "✓" if TG_SESSION_STRING else "✗")
    if not TG_API_ID or not TG_API_HASH:
        logger.error("TG_API_ID / TG_API_HASH не заданы.")
        sys.exit(1)
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if TG_SESSION_STRING:
        client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
    else:
        client = TelegramClient(str(SESSION_PATH), int(TG_API_ID), TG_API_HASH)
    try:
        await client.start()
    except Exception as e:
        logger.error("Не удалось подключиться к Telegram: %s", e)
        return 0
    me = await client.get_me()
    logger.info("Authorized as @%s (id=%d)", me.username, me.id)

    if not os.getenv("_API_MODE"):
        await db.init_db()
    await db.stale_old_reports("benzin_status_bot")

    try:
        bot_entity = await client.get_entity(BOT_USERNAME)
        logger.info("Бот найден: %s (id=%d)", BOT_USERNAME, bot_entity.id)
    except Exception as e:
        logger.error("Не удалось найти бота @%s: %s", BOT_USERNAME, e)
        await client.disconnect()
        return 0

    try:
        total = await query_mini_app(client, bot_entity, cities)
    except Exception as e:
        logger.exception("Ошибка в query_mini_app: %s", e)
        total = 0
    logger.info("=== Total miniapp reports saved: %d ===", total)

    await client.disconnect()
    if not os.getenv("_API_MODE"):
        await db.close_db()
    return total


def main():
    parser = argparse.ArgumentParser(description="Парсер Mini App @benzin_status_bot")
    parser.add_argument("--cities", default=",".join(DEFAULT_CITIES),
                        help="Города через запятую")
    args = parser.parse_args()
    cities = [c.strip() for c in args.cities.split(",") if c.strip()]
    asyncio.run(run(cities))


if __name__ == "__main__":
    main()
