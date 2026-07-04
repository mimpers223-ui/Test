"""
Обогащение адресов через несколько reverse geocoding сервисов.

Стратегия (от быстрого к точному):
  1. Photon (https://photon.komoot.io) — OpenStreetMap, без лимитов,
     быстро, хорошо для Европы. ~5× быстрее Nominatim.
  2. Nominatim (https://nominatim.openstreetmap.org) — точный, но 1 req/s.
  3. Yandex Geocoder (опционально, нужен YANDEX_GEOCODER_API_KEY) —
     лучше для России, лимиты зависят от тарифа.

Использование:
  python scripts/enrich_addresses.py --limit 1000
  python scripts/enrich_addresses.py --limit 10000 --provider photon
  python scripts/enrich_addresses.py --city Иваново --limit 100
  python scripts/enrich_addresses.py --bbox 56.9,57.1,40.8,41.1
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Optional

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402


# === Провайдеры ===

# Photon — без ключа, ~3 req/sec (fair use), OpenStreetMap данные
PHOTON_URL = "https://photon.komoot.io/reverse"
PHOTON_UA = "BenzinRyadom/1.0"

# Nominatim — без ключа, 1 req/sec
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_UA = "BenzinRyadom/1.0 (https://t.me/benzyn_ryadom)"

# Yandex Geocoder — нужен ключ, до 25 000 req/мес бесплатно
YANDEX_URL = "https://geocode-maps.yandex.ru/1.x/"

# BigDataCloud — клиентский endpoint, без ключа, лимит 10 req/sec
BIGDATACLOUD_URL = "https://api.bigdatacloud.net/data/reverse-geocode-client"


async def reverse_bigdatacloud(session: aiohttp.ClientSession, lat: float, lon: float) -> Optional[dict]:
    """Reverse через BigDataCloud (без ключа, client endpoint).
    
    Качество ниже Photon (subdivision вместо city), но быстро и без ключа.
    """
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "localityLanguage": "ru",
        }
        async with session.get(
            BIGDATACLOUD_URL, params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                return None
            data = await r.json()
        return {
            "city": data.get("city") or data.get("locality") or data.get("county") or "",
            "region": data.get("principalSubdivision") or "",
            "street": data.get("streetName") or "",
            "house": data.get("streetNumber") or "",
        }
    except Exception:
        return None


async def reverse_photon(session: aiohttp.ClientSession, lat: float, lon: float) -> Optional[dict]:
    """Reverse через Photon (komoot). Возвращает {city, region, street, house} или None.

    Photon поддерживает lang: default, de, en, fr (НЕ ru — будет HTTP 400).
    Возвращает данные в английской/немецкой локализации.
    """
    try:
        params = {"lon": lon, "lat": lat, "lang": "default"}
        async with session.get(
            PHOTON_URL, params=params,
            headers={"User-Agent": PHOTON_UA},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                return None
            data = await r.json()
        features = data.get("features") or []
        if not features:
            return None
        props = features[0].get("properties") or {}
        return {
            "city": props.get("city") or props.get("town") or props.get("village") or props.get("hamlet") or props.get("suburb") or props.get("county") or "",
            "region": props.get("state") or props.get("region") or "",
            "street": props.get("street") or props.get("road") or props.get("pedestrian") or "",
            "house": props.get("housenumber") or "",
        }
    except Exception:
        return None


async def reverse_nominatim(session: aiohttp.ClientSession, lat: float, lon: float) -> Optional[dict]:
    """Reverse через Nominatim. 1 req/sec."""
    try:
        params = {
            "format": "json", "lat": lat, "lon": lon,
            "accept-language": "ru", "zoom": "18",
        }
        async with session.get(
            NOMINATIM_URL, params=params,
            headers={"User-Agent": NOMINATIM_UA},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                return None
            data = await r.json()
        addr = data.get("address", {})
        return {
            "city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet") or addr.get("suburb") or addr.get("county") or "",
            "region": addr.get("state") or addr.get("region") or "",
            "street": addr.get("road") or addr.get("pedestrian") or addr.get("footway") or "",
            "house": addr.get("house_number", ""),
        }
    except Exception:
        return None


async def reverse_yandex(session: aiohttp.ClientSession, lat: float, lon: float, api_key: str) -> Optional[dict]:
    """Reverse через Yandex Geocoder. Нужен YANDEX_GEOCODER_API_KEY."""
    try:
        params = {
            "format": "json",
            "geocode": f"{lon},{lat}",
            "apikey": api_key,
            "lang": "ru_RU",
            "kind": "house",
            "results": 1,
        }
        async with session.get(
            YANDEX_URL, params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                return None
            data = await r.json()
        members = data.get("response", {}).get("GeoObjectCollection", {}).get("featureMember", [])
        if not members:
            return None
        geo = members[0].get("GeoObject", {})
        meta = geo.get("metaDataProperty", {}).get("GeocoderMetaData", {})
        address = meta.get("Address", {})
        components = address.get("Components", [])
        # Собираем по типам
        result = {"city": "", "region": "", "street": "", "house": ""}
        for c in components:
            kind = c.get("kind", "")
            name = c.get("name", "")
            if kind in ("locality",) and not result["city"]:
                result["city"] = name
            elif kind in ("province", "area") and not result["region"]:
                result["region"] = name
            elif kind in ("street", "district") and not result["street"]:
                result["street"] = name
            elif kind == "house" and not result["house"]:
                result["house"] = name
        # Если city не нашлась в Components — пробуем из formatted адреса
        if not result["city"]:
            formatted = address.get("formatted", "")
            # Берём первое слово до запятой (обычно это город)
            first = formatted.split(",")[0].strip() if formatted else ""
            if first:
                result["city"] = first
        return result
    except Exception:
        return None


async def reverse_with_fallback(
    session: aiohttp.ClientSession,
    lat: float, lon: float,
    provider: str = "photon",
    yandex_key: str = "",
) -> Optional[dict]:
    """Reverse с автоматическим fallback.
    
    Провайдеры:
    - yandex: только Yandex (нужен ключ)
    - nominatim: только Nominatim
    - bigdatacloud: только BigDataCloud
    - photon: только Photon
    - mixed (default): Photon → BigDataCloud → Nominatim → Yandex
    """
    if provider == "yandex" and yandex_key:
        return await reverse_yandex(session, lat, lon, yandex_key)
    if provider == "nominatim":
        return await reverse_nominatim(session, lat, lon)
    if provider == "bigdatacloud":
        return await reverse_bigdatacloud(session, lat, lon)
    if provider == "photon":
        return await reverse_photon(session, lat, lon)
    # mixed: Photon → BigDataCloud → Nominatim → Yandex
    r = await reverse_photon(session, lat, lon)
    if r and r.get("city"):
        return r
    r2 = await reverse_bigdatacloud(session, lat, lon)
    if r2 and r2.get("city"):
        return r2
    r3 = await reverse_nominatim(session, lat, lon)
    if r3 and r3.get("city"):
        return r3
    if yandex_key:
        r4 = await reverse_yandex(session, lat, lon, yandex_key)
        if r4 and r4.get("city"):
            return r4
    return r or r2 or r3  # что нашли, даже без city


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000, help="Лимит АЗС")
    parser.add_argument("--rate", type=float, default=3.0, help="Запросов/сек (Photon)")
    parser.add_argument("--city", help="Фильтр по городу (LIKE)")
    parser.add_argument("--region", help="Фильтр по региону")
    parser.add_argument("--bbox", help="Bbox: 'lat_min,lat_max,lon_min,lon_max'")
    parser.add_argument("--provider",
                        choices=["photon", "nominatim", "yandex", "bigdatacloud", "mixed"],
                        default="mixed",
                        help="Провайдер: mixed=Photon+BigDataCloud+Nominatim+Yandex, photon=только Photon")
    parser.add_argument("--concurrency", type=int, default=5,
                        help="Число параллельных запросов (Photon без ключа — 5-10)")
    parser.add_argument("--checkpoint", default=".enrich_checkpoint",
                        help="Файл для сохранения прогресса (ID последней обработанной АЗС)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    yandex_key = os.getenv("YANDEX_GEOCODER_API_KEY", "")

    print(f"=== Обогащение адресов ===")
    provider_desc = {
        "mixed": f"Photon+BigDataCloud+Nominatim+Yandex" + (f"(Yandex есть)" if yandex_key else "(без Yandex)"),
        "photon": "только Photon",
        "nominatim": "только Nominatim",
        "yandex": "только Yandex" + (f" (ключ: {yandex_key[:8]}...)" if yandex_key else " (⚠ нет ключа!)"),
        "bigdatacloud": "только BigDataCloud",
    }
    print(f"Провайдер: {provider_desc.get(args.provider, args.provider)}")
    print(f"Лимит: {args.limit}, rate: {args.rate} req/s, concurrency: {args.concurrency}")
    if args.city:
        print(f"Фильтр по городу: {args.city}")
    if args.region:
        print(f"Фильтр по региону: {args.region}")
    if args.bbox:
        print(f"Фильтр по bbox: {args.bbox}")

    # Для dry-run тоже инициализируем БД (чтобы загрузить список АЗС), но не пишем
    if not db.API_MODE:
        await db.init_db()

    # Загружаем АЗС без адреса
    where = [
        "lat IS NOT NULL",
        "lon IS NOT NULL",
        "(address IS NULL OR address = '')",
    ]
    params = []
    if args.city:
        idx = len(params) + 1
        where.append(f"(LOWER(city) LIKE ${idx} OR LOWER(name) LIKE ${idx})")
        params.append(f"%{args.city.lower()}%")
    if args.region:
        idx = len(params) + 1
        where.append(f"LOWER(region) LIKE ${idx}")
        params.append(f"%{args.region.lower()}%")
    if args.bbox:
        try:
            lat_min, lat_max, lon_min, lon_max = map(float, args.bbox.split(","))
        except ValueError:
            print(f"❌ Неправильный bbox: {args.bbox}")
            return 1
        idx = len(params) + 1
        where.append(f"lat BETWEEN ${idx} AND ${idx+1}")
        params.extend([lat_min, lat_max])
        idx = len(params) + 1
        where.append(f"lon BETWEEN ${idx} AND ${idx+1}")
        params.extend([lon_min, lon_max])

    # === Checkpoint: пропускаем уже обработанные ===
    checkpoint_path = Path(__file__).parent / args.checkpoint
    skip_until_id = 0
    current_max_id = 0  # для сохранения max ID как checkpoint
    if checkpoint_path.exists() and not args.city and not args.region and not args.bbox:
        try:
            skip_until_id = int(checkpoint_path.read_text().strip())
            current_max_id = skip_until_id
            print(f"📌 Checkpoint: пропускаю до id={skip_until_id}")
        except (ValueError, OSError):
            skip_until_id = 0
    if skip_until_id > 0:
        idx = len(params) + 1
        where.append(f"id > ${idx}")
        params.append(skip_until_id)

    where_str = " AND ".join(where)
    query = f"""
        SELECT id, name, lat, lon
        FROM stations
        WHERE {where_str}
        ORDER BY id
        LIMIT {args.limit}
    """

    rows = await db._fetch(query, *params)
    print(f"Найдено АЗС без адреса: {len(rows)}")

    if not rows:
        print("Все АЗС уже имеют адреса!")
        return 0

    updated = 0
    errors = 0
    no_city = 0
    delay = 1.0 / args.rate if args.rate > 0 else 0
    start = time.time()

    sem = asyncio.Semaphore(args.concurrency)

    async def process_one(session, st):
        """Обрабатывает одну АЗС: reverse + UPDATE."""
        nonlocal updated, errors, no_city, current_max_id
        sid = st["id"]
        lat = st["lat"]
        lon = st["lon"]

        async with sem:
            result = await reverse_with_fallback(
                session, lat, lon,
                provider=args.provider,
                yandex_key=yandex_key,
            )
        if not result or not result.get("city"):
            no_city += 1
            errors += 1
            return False

        street = result.get("street", "")
        house = result.get("house", "")
        city = result.get("city", "")
        region = result.get("region", "")
        full_address = (
            f"{street} {house}".strip()
            if street or house
            else f"{city}, {region}".strip(", ")
        )[:200]

        if not args.dry_run:
            success = False
            for attempt in range(3):
                try:
                    await db._execute(
                        """
                        UPDATE stations
                        SET address = $1, city = COALESCE(NULLIF($2, ''), city), region = COALESCE(NULLIF($3, ''), region)
                        WHERE id = $4
                        """,
                        full_address, city, region, sid,
                    )
                    success = True
                    break
                except Exception as e:
                    if attempt < 2:
                        await asyncio.sleep(2)
                        if not db.API_MODE:
                            try:
                                await db.close_db()
                                await db.init_db()
                            except Exception:
                                pass
                    else:
                        errors += 1
                        print(f"  ⚠ Update {sid}: {e}")
            if success:
                updated += 1
                # Сохраняем checkpoint как MAX (не перезаписываем меньшим)
                if not args.city and not args.region and not args.bbox and sid > current_max_id:
                    current_max_id = sid
                    try:
                        checkpoint_path.write_text(str(current_max_id))
                    except OSError:
                        pass
        return True

    async with aiohttp.ClientSession() as session:
        # Запускаем параллельно, контролируя количество через Semaphore
        tasks = [process_one(session, st) for st in rows]
        for i, fut in enumerate(asyncio.as_completed(tasks), 1):
            await fut
            # Rate limit: пауза между запросами, чтобы не забанили
            if delay > 0:
                await asyncio.sleep(delay)
            if i % 100 == 0:
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(rows) - i) / rate if rate > 0 else 0
                print(
                    f"  Прогресс: {i}/{len(rows)} "
                    f"({rate:.1f} req/s, ETA {eta/60:.0f}m, "
                    f"updated: {updated}, no_city: {no_city})",
                    flush=True,
                )

        if (i + 1) % 100 == 0:
                elapsed = time.time() - start
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                eta = (len(rows) - i - 1) / rate if rate > 0 else 0
                print(
                    f"  Прогресс: {i+1}/{len(rows)} "
                    f"({rate:.1f} req/s, ETA {eta/60:.0f}m, "
                    f"updated: {updated}, no_city: {no_city})"
                )

    elapsed = time.time() - start
    print()
    print(f"=== Итого ===")
    print(f"  Обновлено: {updated}")
    print(f"  Без города: {no_city}")
    print(f"  Ошибок: {errors}")
    print(f"  Время: {elapsed/60:.1f} мин")
    # Удаляем checkpoint если обработали всё
    if updated > 0 and not args.city and not args.region and not args.bbox:
        if checkpoint_path.exists():
            try:
                checkpoint_path.unlink()
                print(f"  🗑 Checkpoint удалён (всё обработано)")
            except OSError:
                pass
    if not db.API_MODE:
        await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
