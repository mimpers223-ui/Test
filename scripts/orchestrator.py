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
parse_all_available = _safe_import("parse_all_available")
parse_quick = _safe_import("parse_quick")
parse_fuel_quality = _safe_import("parse_fuel_quality")
parse_queue_data = _safe_import("parse_queue_data")
parse_limits_canisters = _safe_import("parse_limits_canisters")
parse_gdebenz = _safe_import("parse_gdebenz")
parse_vk_groups = _safe_import("parse_vk_groups")
parse_networks = _safe_import("parse_networks")
parse_benzin_status_tech = _safe_import("parse_benzin_status_tech")
parse_benzin_price = _safe_import("parse_benzin_price")
parse_yandex_fuel = _safe_import("parse_yandex_fuel")
parse_benzinmap = _safe_import("parse_benzinmap")


# Топ городов России по населению (включая Крым, ЛНР, ДНР)
# Полный список всех областных центров + крупные города + Крым/ДНР/ЛНР
TOP_CITIES = [
    # === ЦФО ===
    "moskva",           # Москва
    "voronezh",         # Воронежская обл.
    "belgorod",         # Белгородская обл.
    "bryansk",          # Брянская обл.
    "vladimir",         # Владимирская обл.
    "ivanovo",          # Ивановская обл.
    "kaluga",           # Калужская обл.
    "kostroma",         # Костромская обл.
    "kursk",            # Курская обл.
    "lipetsk",          # Липецкая обл.
    "orel",             # Орловская обл.
    "ryazan",           # Рязанская обл.
    "smolensk",         # Смоленская обл.
    "tambov",           # Тамбовская обл.
    "tver",             # Тверская обл.
    "tula",             # Тульская обл.
    "yaroslavl",        # Ярославская обл.
    # === СЗФО ===
    "sankt-peterburg",  # Санкт-Петербург
    "kaliningrad",      # Калининградская обл.
    "arkhangelsk",      # Архангельская обл.
    "vologda",          # Вологодская обл.
    "murmansk",         # Мурманская обл."
    "novgorod",         # Новгородская обл.
    "pskov",            # Псковская обл.
    "syktyvkar",        # Коми
    "petrozavodsk",     # Карелия
    # === ЮФО ===
    "krasnodar",        # Краснодарский край
    "rostov-na-donu",   # Ростовская обл.
    "astrahan",         # Астраханская обл.
    "volgograd",        # Волгоградская обл.
    "elistа",           # Калмыкия
    "maykop",           # Адыгея
    "sochi",            # Сочи
    # === СКФО ===
    "stavropol",        # Ставропольский край
    "pyatigorsk",       # Пятигорск
    "nalchik",          # Кабардино-Балкария
    "vladikavkaz",      # Северная Осетия
    "grozny",           # Чечня
    "mahachkala",       # Дагестан
    "magas",            # Ингушетия
    "cherkessk",        # Карачаево-Черкесия
    "nalchik",          # Кабардино-Балкария
    # === ПФО ===
    "kazan",            # Татарстан
    "ufa",              # Башкортостан
    "samara",           # Самарская обл.
    "nizhniy-novgorod", # Нижегородская обл.
    "orenburg",         # Оренбургская обл.
    "penza",            # Пензенская обл."
    "perm",             # Пермский край
    "kirov",            # Кировская обл.
    "cheboksary",       # Чувашия
    "izhevsk",          # Удмуртия
    "saransk",          # Мордовия
    "ulyanovск",        # Ульяновская обл.
    "tolyatti",         # Тольятти
    "naberezhnye-chelny", # Набережные Челны
    # === УФО ===
    "ekaterinburg",     # Свердловская обл.
    "chelyabinsk",      # Челябинская обл.
    "tyumen",           # Тюменская обл.
    "kurgan",           # Курганская обл.
    "surgut",           # ХМАО
    "nizhnevartovsk",   # ХМАО
    # === СФО ===
    "novosibirsk",      # Новосибирская обл.
    "omsk",             # Омская обл.
    "krasnoyarsk",      # Красноярский край
    "barnaul",          # Алтайский край
    "kemerovo",         # Кемеровская обл.
    "novokuznetsk",     # Кемеровская обл.
    "tomsk",            # Томская обл.
    "irkutsk",          # Иркутская обл.
    "abakan",           # Хакасия
    "gorno-altaysk",    # Алтай
    # === ДФО ===
    "habarovsk",        # Хабаровский край
    "vladivostok",      # Приморский край
    "yakutsk",          # Якутия
    "blagoveshchensk",  # Амурская обл.
    "chita",            # Забайкальский край
    "nahodka",          # Приморский край
    "ussuriysk",        # Приморский край
    "petropavlovsk-kamchatsky", # Камчатка
    "yuzhno-sakhalinsk", # Сахалинская обл.
    "magадан",          # Магаданская обл.
    "anadyr",           # Чукотка
    # === КРЫМ ===
    "simferopol",       # Крым
    "sevastopol",       # Севастополь
    "kerch",            # Крым
    "yalta",            # Крым
    "evpatoriya",       # Крым
    "feodosiya",        # Крым
    "alushta",          # Крым
    "bahchisaray",      # Крым
    "saki",             # Крым
    "dzhankoy",         # Крым
    "yevpatoria",       # Крым
    # === ДНР ===
    "donetsk",          # ДНР
    "mariupol",         # ДНР
    "makeevka",         # ДНР
    "gorlovka",         # ДНР
    "kramatogorsk",     # ДНР
    "slavyansk",        # ДНР
    "konstantinovka",   # ДНР
    "bakhmut",          # ДНР
    "enakievo",         # ДНР
    "debaltsevo",       # ДНР
    "toretsk",          # ДНР
    "volnovakha",       # ДНР
    "kurakhovo",        # ДНР
    # === ЛНР ===
    "lugansk",          # ЛНР
    "alchevsk",         # ЛНР
    "lisichansk",       # ЛНР
    "severodonetsk",    # ЛНР
    "brыanka",          # ЛНР
    "stakhanov",        # ЛНР
    "krasny-luch",      # ЛНР
    "rubizhne",         # ЛНР
    "popasna",          # ЛНР
    "svatove",          # ЛНР
    "starobilsk",       # ЛНР
    "rovenky",          # ЛНР
    "antratsit",        # ЛНР
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
    if os.getenv("SKIP_TG", "").lower() in ("true", "1", "yes"):
        print("  ⏭ TG: SKIP_TG=true, пропускаю")
        return {}
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
            sys.argv = ["parse_tg_channels.py", "--discover"]
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


async def parse_gdebenz_runner():
    """Запускает gdebenz.ru парсер (2700+ городов)."""
    if not parse_gdebenz:
        print("  ⏭ parse_gdebenz не импортирован")
        return {}
    print(f"\n[gdebenz.ru] 2700+ городов (краудсорсинг)")
    try:
        sys.argv = ["parse_gdebenz.py"]
        await parse_gdebenz.main()
    except SystemExit:
        pass
    except Exception as e:
        print(f"  ⚠ gdebenz: {e}")
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


async def parse_vk_groups_runner():
    """Запускает VK Groups парсер (557 VK-групп, без API ключа)."""
    if not parse_vk_groups:
        print("  ⏭ parse_vk_groups не импортирован")
        return {}
    print(f"\n[vk_groups] 557 VK-групп по городам РФ")
    try:
        from parse_vk_groups import run_vk_parser, VK_FUEL_GROUPS
        cities = list(set(VK_FUEL_GROUPS.values()))
        await run_vk_parser(cities, limit_per_group=30)
    except Exception as e:
        print(f"  ⚠ vk_groups: {e}")
    return {}


async def parse_networks_runner():
    """Запускает парсер сетей АЗС (Лукойл, Газпромнефть, Роснефть, Татнефть)."""
    if not parse_networks:
        print("  ⏭ parse_networks не импортирован")
        return {}
    print(f"\n[networks] Официальные сайты сетей АЗС")
    try:
        sys.argv = ["parse_networks.py", "--network", "all"]
        await parse_networks.main()
    except SystemExit:
        pass
    except Exception as e:
        print(f"  ⚠ networks: {e}")
    return {}


async def parse_benzin_status_tech_runner():
    """Запускает benzin-status.tech парсер (Mini App API, 685+ АЗС Москвы)."""
    if not parse_benzin_status_tech:
        print("  ⏭ parse_benzin_status_tech не импортирован")
        return {}
    print(f"\n[benzin-status.tech] Mini App API, crowd-sourced")
    try:
        sys.argv = ["parse_benzin_status_tech.py"]
        await parse_benzin_status_tech.main()
    except SystemExit:
        pass
    except Exception as e:
        print(f"  ⚠ benzin-status.tech: {e}")
    return {}


async def parse_benzin_price_runner():
    """Запускает benzin-price.ru парсер (28K АЗС, агрегатор цен)."""
    if not parse_benzin_price:
        print("  ⏭ parse_benzin_price не импортирован")
        return {}
    print(f"\n[benzin-price.ru] 28K АЗС, агрегатор цен")
    try:
        sys.argv = ["parse_benzin_price.py", "--region", "all", "--limit", "30"]
        await parse_benzin_price.main()
    except SystemExit:
        pass
    except Exception as e:
        print(f"  ⚠ benzin-price.ru: {e}")
    return {}


async def parse_yandex_fuel_runner():
    """Запускает Яндекс.Заправки (нужен YANDEX_GEOCODER_API_KEY)."""
    if not parse_yandex_fuel:
        print("  ⏭ parse_yandex_fuel не импортирован")
        return {}
    api_key = os.getenv("YANDEX_GEOCODER_API_KEY", "")
    if not api_key:
        print(f"  ⏭ Яндекс.Заправки: YANDEX_GEOCODER_API_KEY не задан, пропускаю")
        return {}
    # Крупные города с координатами
    cities_coords = [
        ("Москва", 55.7558, 37.6173),
        ("СПб", 59.9343, 30.3351),
        ("Новосибирск", 55.0084, 82.9357),
        ("Екатеринбург", 56.8389, 60.6057),
        ("Казань", 55.8304, 49.0661),
        ("Краснодар", 45.0355, 38.9753),
        ("Челябинск", 55.1644, 61.4368),
        ("Самара", 53.1959, 50.1002),
        ("Уфа", 54.7388, 55.9721),
        ("Воронеж", 51.6615, 39.2003),
        ("Ростов-на-Дону", 47.2357, 39.7015),
        ("Волгоград", 48.7194, 44.5018),
        ("Пермь", 58.0105, 56.2502),
        ("Тюмень", 57.1522, 65.5272),
        ("Омск", 54.9885, 73.3242),
        ("Красноярск", 56.0106, 92.8525),
        ("Барнаул", 53.3548, 83.7697),
        ("Иркутск", 52.2864, 104.3057),
        ("Хабаровск", 48.4802, 135.0719),
        ("Владивосток", 43.1198, 131.8869),
        ("Ставрополь", 45.0428, 41.9734),
        ("Пятигорск", 44.0454, 43.0543),
        ("Нальчик", 43.4846, 43.6072),
        ("Владикавказ", 43.0205, 44.6819),
        ("Грозный", 43.3125, 45.6989),
        ("Махачкала", 42.9849, 47.5047),
        # Крым
        ("Симферополь", 44.9521, 34.1024),
        ("Севастополь", 44.6167, 33.5254),
        ("Керчь", 45.3528, 36.4744),
        # ДНР
        ("Донецк", 48.0028, 37.8053),
        ("Мариуполь", 47.0958, 37.5461),
        # ЛНР
        ("Луганск", 48.5740, 39.3078),
    ]
    print(f"\n[yandex_fuel] {len(cities_coords)} городов, Яндекс.Заправки API")
    try:
        sys.argv = ["parse_yandex_fuel.py"]
        for city_name, lat, lon in cities_coords:
            sys.argv = ["parse_yandex_fuel.py", "--lat", str(lat), "--lon", str(lon), "--radius", "15"]
            print(f"  [{city_name}]")
            await parse_yandex_fuel.main()
            await asyncio.sleep(1)
    except SystemExit:
        pass
    except Exception as e:
        print(f"  ⚠ yandex_fuel: {e}")
    return {}


async def parse_benzinmap_runner():
    """Запускает benzinmap.ru парсер (62 региона, лимиты/канистры)."""
    if not parse_benzinmap:
        print("  ⏭ parse_benzinmap не импортирован")
        return {}
    print(f"\n[benzinmap.ru] 62 региона, лимиты/канистры")
    try:
        sys.argv = ["parse_benzinmap.py"]
        await parse_benzinmap.main()
    except SystemExit:
        pass
    except Exception as e:
        print(f"  ⚠ benzinmap: {e}")
    return {}


SOURCES = {
    "gdebenz": {
        "name": "gdebenz.ru (2700+ городов, краудсорсинг наличия)",
        "function": parse_gdebenz_runner,
        "interval_hours": 1,
        "enabled": True,
    },
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
        "interval_hours": 24,
        "enabled": True,
    },
    "ishubenzin": {
        "name": "ishubenzin.ru (народная карта топлива)",
        "function": parse_ishubenzin_runner,
        "interval_hours": 6,
        "enabled": True,
    },
    "all_available": {
        "name": "Все доступные источники (погода, новости, Drom, RIA)",
        "function": lambda: parse_all_available.main() if parse_all_available else asyncio.sleep(0),
        "interval_hours": 1,
        "enabled": True,
    },
    "quick": {
        "name": "Быстрый парсер (2GIS, Drom, weather, news)",
        "function": lambda: parse_quick.main() if parse_quick else asyncio.sleep(0),
        "interval_hours": 1,
        "enabled": True,
    },
    "quality": {
        "name": "Качество топлива (Ростехнадзор, Росстандарт)",
        "function": lambda: parse_fuel_quality.main() if parse_fuel_quality else asyncio.sleep(0),
        "interval_hours": 6,
        "enabled": True,
    },
    "queues": {
        "name": "Данные об очередях (прогнозы, тренды)",
        "function": lambda: parse_queue_data.main() if parse_queue_data else asyncio.sleep(0),
        "interval_hours": 1,
        "enabled": True,
    },
    "limits": {
        "name": "Лимиты и запреты на канистры (Минэнерго, новости, сети АЗС)",
        "function": lambda: parse_limits_canisters.main() if parse_limits_canisters else asyncio.sleep(0),
        "interval_hours": 6,
        "enabled": True,
    },
    "vk_groups": {
        "name": "VK группы (557 сообществ, цены/наличия)",
        "function": parse_vk_groups_runner,
        "interval_hours": 6,
        "enabled": True,
    },
    "networks": {
        "name": "Сети АЗС (Лукойл, Газпромнефть, Роснефть, Татнефть, Башнефть)",
        "function": parse_networks_runner,
        "interval_hours": 12,
        "enabled": True,
    },
    "benzin_status_tech": {
        "name": "benzin-status.tech (Mini App API, 685+ АЗС)",
        "function": parse_benzin_status_tech_runner,
        "interval_hours": 2,
        "enabled": True,
    },
    "benzin_price": {
        "name": "benzin-price.ru (28K АЗС, агрегатор цен)",
        "function": parse_benzin_price_runner,
        "interval_hours": 24,
        "enabled": True,
    },
    "yandex_fuel": {
        "name": "Яндекс.Заправки (API, нужен ключ)",
        "function": parse_yandex_fuel_runner,
        "interval_hours": 12,
        "enabled": True,
    },
    "benzinmap": {
        "name": "benzinmap.ru (62 региона, лимиты/канистры)",
        "function": parse_benzinmap_runner,
        "interval_hours": 6,
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
