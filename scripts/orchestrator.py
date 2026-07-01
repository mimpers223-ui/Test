"""
Оркестратор всех парсеров цен.

Запускает все доступные парсеры, объединяет результаты, обновляет БД.

Источники (в порядке приоритета):
  1. User Reports     — бот (1.0)
  2. TG-каналы        — Telethon (0.85) — нужен auth
  3. fuelprice.ru     — крупный агрегатор (0.75) ✅
  4. Сети АЗС         — официальные сайты (0.75)
  5. benzin-price.ru  — JS-challenge (нужен headless)
  6. 2ГИС paid        — полные цены (0.80)
  7. 2ГИС demo        — координаты (0.40)
  8. OSM              — fallback (0.30)

Запуск:
  python scripts/orchestrator.py --once
  python scripts/orchestrator.py --once --only fuelprice,enrich   # только указанные
  python scripts/orchestrator.py --schedule    # каждые 6 часов

Расписание (по умолчанию):
  - fuelprice.ru: раз в сутки
  - сети: каждые 6 часов
  - TG: каждые 2 часа
  - benzin-price.ru: раз в сутки (когда будет headless)
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402


def _safe_import(name: str):
    """Импорт с обработкой отсутствующих модулей (telethon, playwright и т.п.)."""
    try:
        mod = __import__(name)
        return mod
    except ImportError as e:
        print(f"  ⏭ {name}: модуль не установлен ({e})")
        return None
    except Exception as e:
        print(f"  ⏭ {name}: ошибка импорта ({e})")
        return None


# === Импортируем парсеры (мягко) ===
parse_fuelprice = _safe_import("parse_fuelprice")
parse_vk = _safe_import("parse_vk")
parse_tg_channels = _safe_import("parse_tg_channels")
parse_tg_prices = _safe_import("parse_tg_prices")
parse_2gis = _safe_import("parse_2gis")
parse_osm = _safe_import("parse_osm")
parse_max = _safe_import("parse_max")
parse_yandex_maps = _safe_import("parse_yandex_maps_playwright")
parse_ishubenzin = _safe_import("parse_ishubenzin")
enrich_addresses = _safe_import("enrich_addresses")


# Топ-12 городов России по населению
TOP_CITIES = [
    "moskva",
    "sankt-peterburg",
    "novosibirsk",
    "ekaterinburg",
    "kazan",
    "krasnodar",
    "chelyabinsk",
    "nizhniy-novgorod",
    "samara",
    "rostov-na-donu",
    "ufa",
    "krasnoyarsk",
]


async def parse_fuelprice_all_cities():
    """Запускает fuelprice.ru по всем крупным городам."""
    if not parse_fuelprice:
        print("  ⏭ parse_fuelprice не импортирован")
        return {}
    print(f"\n[fuelprice.ru] {len(TOP_CITIES)} городов")
    for city in TOP_CITIES:
        try:
            sys.argv = ["parse_fuelprice.py", "--city", city, "--create-new"]
            await parse_fuelprice.main()
        except SystemExit:
            pass
        except Exception as e:
            print(f"  ⚠ {city}: {e}")
        await asyncio.sleep(2)
    return {}


async def parse_vk_runner():
    """Запускает VK-парсер (web или API в зависимости от токена)."""
    if not parse_vk:
        print("  ⏭ parse_vk не импортирован")
        return {}
    token = os.getenv("VK_SERVICE_TOKEN", "")
    if token:
        # Режим API
        groups = os.getenv("VK_GROUPS", "avto_benzin,fuel_price,autotoplivo,toplivo_prices")
        sys.argv = ["parse_vk.py", "--api", "--groups", groups, "--limit", "50"]
    else:
        # Режим web
        sys.argv = ["parse_vk.py", "--query", "АИ-95 цена", "--limit", "20"]
    try:
        await parse_vk.main()
    except SystemExit:
        pass
    except Exception as e:
        print(f"  ⚠ VK парсер: {e}")
    return {}


async def parse_tg_runner():
    """Запускает Telethon-парсеры (если есть credentials)."""
    if not parse_tg_channels and not parse_tg_prices:
        print("  ⏭ TG: telethon не установлен (pip install telethon)")
        return {}
    api_id = os.getenv("TG_API_ID", "")
    api_hash = os.getenv("TG_API_HASH", "")
    if not api_id or not api_hash:
        print(f"  ⏭ TG: TG_API_ID/TG_API_HASH не заданы, пропускаю")
        return {}
    if parse_tg_channels:
        try:
            sys.argv = ["parse_tg_channels.py", "--limit", "50"]
            await parse_tg_channels.main()
        except SystemExit:
            pass
        except Exception as e:
            print(f"  ⚠ TG channels: {e}")
    if parse_tg_prices:
        try:
            sys.argv = ["parse_tg_prices.py", "--all", "--limit", "30"]
            await parse_tg_prices.main()
        except SystemExit:
            pass
        except Exception as e:
            print(f"  ⚠ TG prices: {e}")
    return {}


async def parse_2gis_runner():
    """Запускает 2ГИС-парсер (если есть ключ)."""
    if not parse_2gis:
        print("  ⏭ parse_2gis не импортирован")
        return {}
    api_key = os.getenv("TWO_GIS_API_KEY", "")
    if not api_key:
        print(f"  ⏭ 2ГИС: TWO_GIS_API_KEY не задан, пропускаю")
        return {}
    try:
        cities = ["Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань", "Краснодар"]
        for city in cities:
            sys.argv = ["parse_2gis.py", "--city", city, "--limit", "100"]
            await parse_2gis.main()
            await asyncio.sleep(2)
    except SystemExit:
        pass
    except Exception as e:
        print(f"  ⚠ 2ГИС: {e}")
    return {}


async def parse_max_runner():
    """Запускает MAX-парсер (если есть токен и добавлены каналы)."""
    if not parse_max:
        print("  ⏭ parse_max не импортирован")
        return {}
    token = os.getenv("MAX_BOT_TOKEN", "")
    if not token:
        print(f"  ⏭ MAX: MAX_BOT_TOKEN не задан, пропускаю")
        return {}
    try:
        sys.argv = ["parse_max.py", "--all", "--limit", "50"]
        await parse_max.main()
    except SystemExit:
        pass
    except Exception as e:
        print(f"  ⚠ MAX: {e}")
    return {}


async def parse_yandex_maps_runner():
    """Запускает Яндекс.Карты парсер (POI АЗС).
    
    Без API ключа — Playwright, медленно, риск бана.
    С API ключом — HTTP API, быстро, лимит 1К/день.
    """
    if not parse_yandex_maps:
        print("  ⏭ parse_yandex_maps_playwright не импортирован")
        return {}
    api_key = os.getenv("YANDEX_MAPS_API_KEY", "")
    cities = os.getenv("YANDEX_CITIES", "Иваново,Ярославль,Кострома").split(",")
    print(f"\n[Яндекс.Карты] {len(cities)} городов, режим: {'HTTP API' if api_key else 'Playwright'}")
    for city in cities:
        city = city.strip()
        if not city:
            continue
        try:
            argv = ["parse_yandex_maps_playwright.py", "--city", city, "--limit", "30"]
            if api_key:
                argv.extend(["--api-key", api_key])
            sys.argv = argv
            await parse_yandex_maps.main()
        except SystemExit:
            pass
        except Exception as e:
            print(f"  ⚠ {city}: {e}")
        await asyncio.sleep(3)
    return {}


async def enrich_runner():
    """Обогащение адресов: 200 АЗС за раз (для schedule)."""
    if not enrich_addresses:
        print("  ⏭ enrich_addresses не импортирован")
        return {}
    print(f"\n[enrich_addresses] Photon (быстрый, без ключа)")
    try:
        sys.argv = ["enrich_addresses.py", "--limit", "200", "--provider", "photon"]
        await enrich_addresses.main()
    except SystemExit:
        pass
    except Exception as e:
        print(f"  ⚠ enrich_addresses: {e}")
    return {}


async def parse_ishubenzin_runner():
    """Запускает ishubenzin.ru парсер (народная карта топлива, без ключа)."""
    if not parse_ishubenzin:
        print("  ⏭ parse_ishubenzin не импортирован")
        return {}
    print(f"\n[ishubenzin.ru] Crowd-sourced fuel map (no API key)")
    try:
        sys.argv = ["parse_ishubenzin.py"]
        await parse_ishubenzin.main()
    except SystemExit:
        pass
    except Exception as e:
        print(f"  ⚠ ishubenzin.ru: {e}")
    return {}


SOURCES = {
    "fuelprice": {
        "name": "fuelprice.ru (60+ городов, координаты + цены)",
        "function": parse_fuelprice_all_cities,
        "interval_hours": 24,
        "enabled": True,
    },
    "vk": {
        "name": "VK (паблики/поиск с ценами на бензин)",
        "function": parse_vk_runner,
        "interval_hours": 6,
        "enabled": True,
    },
    "tg": {
        "name": "Telegram-каналы (Telethon, нужен auth)",
        "function": parse_tg_runner,
        "interval_hours": 2,
        "enabled": True,
    },
    "2gis": {
        "name": "2ГИС (paid API, нужен ключ)",
        "function": parse_2gis_runner,
        "interval_hours": 12,
        "enabled": True,
    },
    "max": {
        "name": "MAX (мессенджер, Bot API, нужен токен)",
        "function": parse_max_runner,
        "interval_hours": 6,
        "enabled": True,
    },
    "yandex_maps": {
        "name": "Яндекс.Карты (POI АЗС, нужен API ключ для скорости)",
        "function": parse_yandex_maps_runner,
        "interval_hours": 24,
        "enabled": True,
    },
    "enrich": {
        "name": "Обогащение адресов (Photon)",
        "function": enrich_runner,
        "interval_hours": 6,
        "enabled": True,
    },
    "ishubenzin": {
        "name": "ishubenzin.ru (народная карта, без ключа)",
        "function": parse_ishubenzin_runner,
        "interval_hours": 4,
        "enabled": True,
    },
}


async def run_source(name: str, source: dict) -> bool:
    """Запускает один источник."""
    print(f"\n>>> {source['name']}")
    print(f"    Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        result = await source["function"]()
        print(f"    ✓ Завершено")
        return True
    except Exception as e:
        print(f"    ❌ Ошибка: {e}")
        return False


async def run_once(only: list[str] | None = None) -> None:
    """Запускает все (или указанные) источники один раз.

    only: список имён источников для запуска (None = все enabled).
    """
    print("=" * 60)
    print(f"ОРКЕСТРАТОР ПАРСЕРОВ — однократный запуск")
    print(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    await db.init_db()

    results = {}
    for name, source in SOURCES.items():
        if not source["enabled"]:
            continue
        if only and name not in only:
            continue
        results[name] = await run_source(name, source)

    print()
    print("=" * 60)
    print("ИТОГО")
    print("=" * 60)
    for name, ok in results.items():
        status = "✓" if ok else "❌"
        print(f"  {status} {SOURCES[name]['name']}")

    await db.close_db()


async def run_schedule():
    """Запускает парсеры по расписанию."""
    print("=" * 60)
    print("ОРКЕСТРАТОР ПАРСЕРОВ — режим расписания")
    print(f"Старт: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    await db.init_db()

    last_run = {name: None for name in SOURCES}

    while True:
        now = datetime.now()
        for name, source in SOURCES.items():
            if not source["enabled"]:
                continue
            interval = timedelta(hours=source["interval_hours"])
            last = last_run[name]
            if last is None or (now - last) >= interval:
                await run_source(name, source)
                last_run[name] = now

        # Спим 10 минут между проверками
        await asyncio.sleep(600)

    await db.close_db()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Однократный запуск")
    parser.add_argument("--schedule", action="store_true", help="Запуск по расписанию")
    parser.add_argument("--only", help="Только указанные источники (через запятую)")
    args = parser.parse_args()

    if args.schedule:
        asyncio.run(run_schedule())
    else:
        # По умолчанию — однократный запуск
        only = [s.strip() for s in (args.only or "").split(",") if s.strip()] or None
        asyncio.run(run_once(only=only))


if __name__ == "__main__":
    main()
