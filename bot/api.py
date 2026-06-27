"""
API-сервер для Mini App.
Работает рядом с ботом в одном процессе (порт 8080).
"""
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

from db import (
    USE_SQLITE,
    add_report,
    find_nearest_stations,
    find_stations_by_city,
    find_stations_by_name,
    get_all_prices_for_station,
    get_station_analytics,
    get_station_by_id,
    get_station_current_status,
    get_user_id_by_telegram_id,
    upsert_user,
    check_and_award_badges,
    BADGE_CATALOG,
)
import db  # for db._fetch, db.USE_SQLITE в get_source_stats
import aiohttp  # для reverse geocoding

logger = logging.getLogger(__name__)


# === Rate limit (in-memory, на IP) ===
# Простой token bucket: max N запросов в минуту на IP
_rate_limit: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_PER_MIN = 60  # 60 GET / 30 POST в минуту на IP


def _check_rate(ip: str, max_per_min: int) -> bool:
    """Возвращает True если запрос разрешён, False если rate limit превышен."""
    now = time.time()
    # Чистим старые записи (>60 сек)
    _rate_limit[ip] = [t for t in _rate_limit[ip] if now - t < 60]
    if len(_rate_limit[ip]) >= max_per_min:
        return False
    _rate_limit[ip].append(now)
    return True


def _serialize_station(s: dict) -> dict:
    """Приводит станцию к JSON-безопасному виду."""
    from datetime import datetime, date
    from decimal import Decimal
    out = dict(s)
    if "fuel_types" in out and isinstance(out["fuel_types"], str):
        try:
            out["fuel_types"] = json.loads(out["fuel_types"])
        except Exception:
            out["fuel_types"] = []
    # datetime → ISO string, Decimal → float (asyncpg/PostgreSQL)
    for k, v in list(out.items()):
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
    return out


def _serialize_status(s: dict) -> dict:
    from datetime import datetime, date
    from decimal import Decimal
    out = dict(s)
    if "available" in out:
        out["available"] = bool(out["available"]) if out["available"] is not None else None
    if "has_limit" in out:
        out["has_limit"] = bool(out["has_limit"])
    # datetime → ISO string, Decimal → float
    for k, v in list(out.items()):
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
    return out


def _parse_float(request, name: str, min_val: float, max_val: float) -> tuple[float | None, web.Response | None]:
    """Парсит float query param с валидацией диапазона."""
    try:
        v = float(request.query[name])
    except (KeyError, ValueError):
        return None, web.json_response(
            {"error": f"{name} is required and must be a number"},
            status=400,
        )
    if not (min_val <= v <= max_val):
        return None, web.json_response(
            {"error": f"{name} must be in [{min_val}, {max_val}]"},
            status=400,
        )
    return v, None


# === Handlers ===
async def handle_health(request):
    return web.json_response({"status": "ok"})


async def handle_logs(request):
    """GET /api/logs?lines=50 — последние строки bot.log (для отладки)."""
    log_path = Path(__file__).parent / "bot.log"
    if not log_path.exists():
        return web.json_response({"error": "no log file"}, status=404)
    try:
        lines = int(request.query.get("lines", "50"))
        lines = max(1, min(lines, 500))
    except (ValueError, TypeError):
        lines = 50
    try:
        # Читаем последние N строк
        with open(log_path, "rb") as f:
            content = f.read()
        text = content.decode("utf-8", errors="ignore")
        all_lines = text.splitlines()
        last = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return web.json_response({
            "path": str(log_path),
            "total_lines": len(all_lines),
            "shown": len(last),
            "lines": last,
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# === Кеш reverse geocoding (city по координатам) ===
_reverse_cache: dict[tuple[float, float], dict] = {}


async def handle_reverse_geocode(request):
    """GET /api/reverse-geocode?lat=..&lon=..

    Возвращает город и регион по координатам (Nominatim).
    Используется Mini App для автоопределения города.
    """
    lat, err = _parse_float(request, "lat", -90, 90)
    if err:
        return err
    lon, err = _parse_float(request, "lon", -180, 180)
    if err:
        return err

    # Кеш (округление до 0.01 ≈ 1.1 км)
    cache_key = (round(lat, 2), round(lon, 2))
    if cache_key in _reverse_cache:
        return web.json_response(_reverse_cache[cache_key])

    try:
        url = (
            f"https://nominatim.openstreetmap.org/reverse"
            f"?format=json&lat={lat}&lon={lon}&accept-language=ru&zoom=10"
        )
        headers = {"User-Agent": "BenzinRyadom/1.0 (https://t.me/benzyn_ryadom)"}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10), headers=headers) as r:
                if r.status == 200:
                    data = await r.json()
                    addr = data.get("address", {})
                    city = (
                        addr.get("city")
                        or addr.get("town")
                        or addr.get("village")
                        or addr.get("hamlet")
                        or addr.get("county")
                    )
                    region = addr.get("state") or addr.get("region")
                    result = {
                        "city": city,
                        "region": region,
                        "country": addr.get("country"),
                        "raw": addr,
                    }
                    # Кешируем
                    if len(_reverse_cache) > 1000:
                        _reverse_cache.clear()
                    _reverse_cache[cache_key] = result
                    return web.json_response(result)
    except Exception as e:
        pass

    # Fallback: не нашли
    return web.json_response({"city": None, "region": None, "country": None})


async def handle_admin_stats(request):
    """GET /api/admin/stats — статистика всех парсеров (мониторинг).

    Возвращает:
    - Сколько цен за 1/6/24 часа по источникам
    - Когда был последний отчёт
    - Статус каждого парсера (OK / STALE / DEAD)
    - Сколько АЗС в базе
    - Сколько АЗС с ценами
    """
    # === Статистика по источникам ===
    sources_stats = await get_source_stats()
    total_stations = await db._fetch("SELECT COUNT(*) as c FROM stations", one=True)
    with_prices = await db._fetch("""
        SELECT COUNT(DISTINCT station_id) as c
        FROM reports
        WHERE created_at > NOW() - INTERVAL '7 days'
    """, one=True)

    return web.json_response({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stations": {
            "total": total_stations["c"],
            "with_prices_7d": with_prices["c"],
        },
        "sources": sources_stats,
    })


async def get_source_stats() -> list[dict]:
    """Собирает статистику по каждому источнику."""
    rows = await db._fetch("""
        SELECT source,
               COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as h1,
               COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '6 hours') as h6,
               COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as h24,
               COUNT(*) as total,
               MAX(created_at) as last_update
        FROM reports
        GROUP BY source
        ORDER BY total DESC
    """)
    result = []
    for r in rows:
        # Статус: OK (1h), STALE (6h), DEAD (24h+)
        last = r["last_update"]
        hours_ago = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        if hours_ago < 1:
            status = "OK"
        elif hours_ago < 6:
            status = "STALE"
        else:
            status = "DEAD"
        result.append({
            "source": r["source"],
            "h1": r["h1"],
            "h6": r["h6"],
            "h24": r["h24"],
            "total": r["total"],
            "last_update": last.isoformat(),
            "hours_ago": round(hours_ago, 1),
            "status": status,
        })
    return result


async def handle_stations(request):
    """GET /api/stations?lat=..&lon=..&radius=..&fuel=92&telegram_id=.."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return web.json_response({"error": "rate limit exceeded"}, status=429)

    lat, err = _parse_float(request, "lat", -90, 90)
    if err:
        return err
    lon, err = _parse_float(request, "lon", -180, 180)
    if err:
        return err

    # === Premium detection по telegram_id ===
    telegram_id_raw = request.query.get("telegram_id")
    is_premium_user = False
    if telegram_id_raw:
        try:
            tid = int(telegram_id_raw)
            uid = await get_user_id_by_telegram_id(tid)
            if uid:
                from db import is_premium
                is_premium_user = await is_premium(uid)
        except (ValueError, TypeError):
            pass

    # === Premium лимиты ===
    max_radius = 100 if is_premium_user else 30
    max_limit = 500 if is_premium_user else 100
    default_radius = 50 if is_premium_user else 30

    try:
        radius = int(request.query.get("radius", default_radius))
        if not (1 <= radius <= max_radius):
            return web.json_response(
                {"error": f"radius must be in [1, {max_radius}]"}, status=400
            )
    except ValueError:
        return web.json_response({"error": "radius must be int"}, status=400)

    fuel = request.query.get("fuel")
    if fuel is not None and fuel not in ("92", "95", "98", "diesel", "100", "lpg"):
        return web.json_response({"error": f"invalid fuel: {fuel}"}, status=400)

    stations = await find_nearest_stations(
        lat=lat, lon=lon, fuel_type=fuel, limit=max_limit, radius_km=radius,
    )

    # Один запрос на статусы для всех АЗС (избегаем N+1)
    station_ids = [s["id"] for s in stations]
    statuses_by_station = await _bulk_get_statuses(station_ids)

    result = []
    for s in stations:
        sid = s["id"]
        statuses = statuses_by_station.get(sid, [])
        # Если operator пустой — используем name (многие АЗС имеют только name)
        operator = s.get("operator") or s.get("name")
        # Если city пустой — оставляем пустым
        result.append({
            "id": sid,
            "name": s.get("name"),
            "operator": operator,
            "city": s.get("city"),
            "address": s.get("address") or "",
            "lat": s.get("lat"),
            "lon": s.get("lon"),
            "distance_km": s.get("distance_km"),
            "is_verified": bool(s.get("is_verified")),
            "statuses": [_serialize_status(st) for st in statuses],
            "has_data": len(statuses) > 0,
        })

    return web.json_response({"stations": result, "count": len(result)})


# === Дисклеймер ===
DISCLAIMER = (
    "⚠️ <b>Важно:</b>\n"
    "• Цены и наличие обновляются пользователями и парсерами, возможны задержки.\n"
    "• Актуальность зависит от региона: крупные города — точнее, малые — реже.\n"
    "• Перед поездкой перезвоните на АЗС, особенно если топливо подорожало.\n"
    "• Данные собираются из: fuelprice.ru, 2ГИС, отчётов пользователей, "
    "Telegram-каналов и других открытых источников.\n"
    "• Бот не несёт ответственности за достоверность данных."
)


async def handle_stations_by_city(request):
    """GET /api/stations/by-city?city=...&region=...&fuel=...&network=...&max_price=...&has_stock=1

    Возвращает АЗС по городу (а не геолокации), с фильтрами:
      - city: название города (обязательно)
      - region: регион (опционально)
      - fuel: 92/95/98/diesel/lpg
      - network: оператор (Лукойл, Газпром, etc)
      - max_price: макс. цена за литр
      - has_stock: 1 = только с подтверждённым наличием (default 1)
      - include_nearby_regions: 1 = включать соседние регионы (default 1)
      - limit: макс. кол-во результатов (default 50)
      - telegram_id: для Premium detection
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return web.json_response({"error": "rate limit exceeded"}, status=429)

    city = (request.query.get("city") or "").strip()
    if not city:
        return web.json_response({"error": "city is required"}, status=400)

    region = request.query.get("region") or None
    fuel = request.query.get("fuel") or None
    network = request.query.get("network") or None
    has_stock = request.query.get("has_stock", "1") == "1"
    include_nearby = request.query.get("include_nearby_regions", "1") == "1"

    try:
        max_price = float(request.query["max_price"]) if "max_price" in request.query else None
    except (ValueError, KeyError):
        max_price = None

    try:
        limit = int(request.query.get("limit", "50"))
        limit = max(1, min(limit, 500))
    except ValueError:
        limit = 50

    # === Premium detection ===
    telegram_id_raw = request.query.get("telegram_id")
    is_premium_user = False
    if telegram_id_raw:
        try:
            tid = int(telegram_id_raw)
            uid = await get_user_id_by_telegram_id(tid)
            if uid:
                is_premium_user = await is_premium(uid)
        except (ValueError, TypeError):
            pass

    if is_premium_user:
        limit = min(limit * 3, 500)

    stations = await find_stations_by_city(
        city=city,
        region=region,
        fuel_type=fuel,
        network=network,
        max_price=max_price,
        has_stock=has_stock,
        include_nearby_regions=include_nearby,
        limit=limit,
    )

    # Получаем статусы (цены + наличие)
    station_ids = [s["id"] for s in stations]
    statuses_by_station = await _bulk_get_statuses(station_ids)

    result = []
    for s in stations:
        sid = s["id"]
        statuses = statuses_by_station.get(sid, [])
        result.append({
            "id": sid,
            "name": s.get("name"),
            "operator": s.get("operator"),
            "city": s.get("city"),
            "region": s.get("region"),
            "address": s.get("address"),
            "lat": s.get("lat"),
            "lon": s.get("lon"),
            "is_verified": bool(s.get("is_verified")),
            "statuses": [_serialize_status(st) for st in statuses],
            "has_data": len(statuses) > 0,
        })

    return web.json_response({
        "stations": result,
        "count": len(result),
        "city": city,
        "filters": {
            "region": region,
            "fuel": fuel,
            "network": network,
            "max_price": max_price,
            "has_stock": has_stock,
            "include_nearby_regions": include_nearby,
        },
        "disclaimer": DISCLAIMER.replace("<b>", "").replace("</b>", ""),
    })


async def handle_search(request):
    """GET /api/search?q=... — поиск АЗС по городу/имени."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return web.json_response({"error": "rate limit exceeded"}, status=429)

    query = request.query.get("q", "").strip()
    if len(query) < 2:
        return web.json_response(
            {"error": "q parameter required (min 2 chars)"},
            status=400,
        )

    stations = await find_stations_by_name(query, limit=30)

    station_ids = [s["id"] for s in stations]
    statuses_by_station = await _bulk_get_statuses(station_ids)

    result = []
    for s in stations:
        sid = s["id"]
        statuses = statuses_by_station.get(sid, [])
        result.append({
            "id": sid,
            "name": s.get("name"),
            "operator": s.get("operator"),
            "city": s.get("city"),
            "lat": s.get("lat"),
            "lon": s.get("lon"),
            "is_verified": bool(s.get("is_verified")),
            "statuses": [_serialize_status(st) for st in statuses],
            "has_data": len(statuses) > 0,
        })

    return web.json_response({
        "stations": result,
        "count": len(result),
        "is_premium": is_premium_user,
        "limits": {
            "max_radius": max_radius,
            "max_stations": max_limit,
        },
    })


async def _bulk_get_statuses(station_ids: list[int]) -> dict[int, list]:
    """Один запрос на получение статусов для многих АЗС. Избегаем N+1."""
    if not station_ids:
        return {}
    from db import _fetch
    placeholders = ",".join("?" for _ in station_ids)
    if USE_SQLITE:
        rows = await _fetch(
            f"""SELECT station_id, fuel_type, available, price, queue_size, has_limit,
                      limit_liters, confidence, created_at
               FROM (
                   SELECT *, ROW_NUMBER() OVER (
                       PARTITION BY station_id, fuel_type
                       ORDER BY confidence DESC, created_at DESC
                   ) AS rn
                   FROM reports
                   WHERE station_id IN ({placeholders})
                     AND created_at > datetime('now', '-1 day')
               )
               WHERE rn = 1""",
            *station_ids,
        )
    else:
        # PostgreSQL: DISTINCT ON работает
        rows = await _fetch(
            f"""SELECT DISTINCT ON (station_id, fuel_type)
                    station_id, fuel_type, available, price, queue_size,
                    has_limit, limit_liters, confidence, created_at
                FROM reports
                WHERE station_id = ANY($1)
                  AND created_at > NOW() - INTERVAL '24 hours'
                ORDER BY station_id, fuel_type, confidence DESC, created_at DESC""",
            list(station_ids),
        )

    # Конвертируем SQLite int → bool/None
    result: dict[int, list] = {}
    for r in rows:
        sid = r["station_id"]
        if r.get("available") == 1:
            r["available"] = True
        elif r.get("available") == 0:
            r["available"] = False
        elif r.get("available") == 2:
            r["available"] = None
        result.setdefault(sid, []).append(r)
    return result


async def handle_station_detail(request):
    """GET /api/stations/{id}"""
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return web.json_response({"error": "rate limit exceeded"}, status=429)

    try:
        station_id = int(request.match_info["id"])
    except ValueError:
        return web.json_response({"error": "invalid id"}, status=400)

    station = await get_station_by_id(station_id)
    if not station:
        return web.json_response({"error": "not found"}, status=404)

    statuses = await get_station_current_status(station_id)
    return web.json_response({
        "station": _serialize_station(station),
        "statuses": [_serialize_status(st) for st in statuses],
    })


async def handle_price_history(request):
    """GET /api/stations/{id}/price-history?fuel=92&days=30"""
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return web.json_response({"error": "rate limit exceeded"}, status=429)

    try:
        station_id = int(request.match_info["id"])
    except ValueError:
        return web.json_response({"error": "invalid id"}, status=400)

    fuel = request.query.get("fuel", "95")
    if fuel not in ("92", "95", "98", "diesel", "100", "lpg"):
        return web.json_response({"error": f"invalid fuel: {fuel}"}, status=400)

    try:
        days = int(request.query.get("days", "30"))
        if not (1 <= days <= 365):
            return web.json_response({"error": "days must be in [1, 365]"}, status=400)
    except ValueError:
        return web.json_response({"error": "days must be int"}, status=400)

    from db import _fetch
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT fuel_type, price, created_at
               FROM reports
               WHERE station_id = ? AND fuel_type = ? AND price IS NOT NULL
                 AND created_at > datetime('now', ?)
               ORDER BY created_at DESC
               LIMIT 50""",
            station_id, fuel, f"-{days} days",
        )
    else:
        rows = await _fetch(
            """SELECT fuel_type, price, created_at
               FROM reports
               WHERE station_id = $1 AND fuel_type = $2 AND price IS NOT NULL
                 AND created_at > NOW() - ($3 || ' days')::interval
               ORDER BY created_at DESC
               LIMIT 50""",
            station_id, fuel, str(days),
        )

    history = []
    for r in rows:
        history.append({
            "fuel_type": r.get("fuel_type"),
            "price": float(r["price"]) if r.get("price") is not None else None,
            "at": str(r.get("created_at")),
        })

    return web.json_response({
        "station_id": station_id,
        "fuel": fuel,
        "history": history,
        "count": len(history),
    })


async def handle_station_analytics(request):
    """GET /api/stations/{id}/analytics — аналитика для владельца АЗС."""
    try:
        station_id = int(request.match_info["id"])
    except (KeyError, ValueError, TypeError):
        return web.json_response({"error": "invalid id"}, status=400)

    days = int(request.query.get("days", 30))
    if days < 1 or days > 365:
        days = 30

    analytics = await get_station_analytics(station_id, days)
    return web.json_response(analytics)


async def handle_premium_status(request):
    """GET /api/premium-status?tg=<telegram_id> — статус Premium для Mini App."""
    try:
        tg = int(request.query.get("tg", "0"))
    except (ValueError, TypeError):
        return web.json_response({"is_premium": False, "error": "invalid tg"}, status=400)
    if not tg:
        return web.json_response({"is_premium": False})

    uid = await get_user_id_by_telegram_id(tg)
    if not uid:
        return web.json_response({"is_premium": False})

    is_prem = await is_premium(uid)
    info = await get_premium_info(uid) if is_prem else None
    days_left = 0
    if info and info.get("expires_at"):
        try:
            from datetime import datetime
            exp = info["expires_at"]
            if isinstance(exp, str):
                exp_dt = datetime.fromisoformat(exp)
            else:
                exp_dt = exp
            days_left = max(0, (exp_dt - datetime.now()).days)
        except Exception:
            pass

    return web.json_response({
        "is_premium": is_prem,
        "days_left": days_left,
        "expires_at": str(info["expires_at"])[:10] if info else None,
    })


async def handle_station_prices(request):
    """GET /api/stations/{id}/prices — все цены по источникам с приоритетом.

    Возвращает:
    {
      "station_id": 1,
      "fuel_prices": {
        "95": {
          "best": {"source": "user", "price": 56.40, "confidence": 0.92, "age_hours": 0.5},
          "all": [
            {"source": "user", "price": 56.40, "is_best": true, "confidence": 0.92, "age_hours": 0.5},
            {"source": "2gis", "price": 56.20, "is_best": false, "confidence": 0.65, "age_hours": 24.0}
          ]
        }
      },
      "sources_summary": {
        "user": 5,        # сколько отчётов
        "telegram": 2,
        "2gis": 1
      }
    }
    """
    try:
        station_id = int(request.match_info["id"])
    except (KeyError, ValueError, TypeError):
        return web.json_response({"error": "invalid id"}, status=400)

    all_prices = await get_all_prices_for_station(station_id)

    # Форматируем для Mini App
    from datetime import datetime, date
    from decimal import Decimal
    fuel_prices = {}
    sources_summary = {}
    for fuel, items in all_prices.items():
        if not items:
            continue
        # Лучший — items[0] (отсортированы по weighted_score)
        best = items[0]

        def _to_jsonable(v):
            if isinstance(v, (datetime, date)):
                return v.isoformat()
            if isinstance(v, Decimal):
                return float(v)
            return v

        fuel_prices[fuel] = {
            "best": {
                "source": best.get("source"),
                "price": _to_jsonable(best.get("price")),
                "confidence": _to_jsonable(best.get("weighted_score")),
                "age_hours": _to_jsonable(best.get("age_hours")),
                "updated_at": _to_jsonable(best.get("created_at")),
            },
            "all": [
                {
                    "source": it.get("source"),
                    "price": _to_jsonable(it.get("price")),
                    "is_best": it.get("is_best", False),
                    "confidence": _to_jsonable(it.get("weighted_score")),
                    "age_hours": _to_jsonable(it.get("age_hours")),
                    "updated_at": _to_jsonable(it.get("created_at")),
                }
                for it in items[:5]  # максимум 5 источников
            ],
        }
        # Считаем по источникам
        for it in items:
            src = it.get("source") or "default"
            sources_summary[src] = sources_summary.get(src, 0) + 1

    return web.json_response({
        "station_id": station_id,
        "fuel_prices": fuel_prices,
        "sources_summary": sources_summary,
        "total_sources": len(sources_summary),
    })


async def handle_create_report(request):
    """POST /api/reports — создание отчёта из Mini App"""
    # Строже rate limit для POST
    if not _check_rate(request.remote or "?", 30):
        return web.json_response({"error": "rate limit exceeded"}, status=429)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    if not isinstance(data, dict):
        return web.json_response({"error": "expected json object"}, status=400)

    station_id = data.get("station_id")
    fuel_type = data.get("fuel_type")
    available = data.get("available")
    telegram_id = data.get("telegram_id")
    first_name = str(data.get("first_name", "MiniApp User"))[:64]
    price = data.get("price")
    queue_size = data.get("queue_size")
    has_limit = data.get("has_limit", False)
    limit_liters = data.get("limit_liters")

    if not station_id or not isinstance(station_id, int):
        return web.json_response({"error": "station_id (int) is required"}, status=400)
    if not fuel_type or fuel_type not in ("92", "95", "98", "diesel", "100", "lpg"):
        return web.json_response({"error": f"invalid fuel_type: {fuel_type}"}, status=400)
    if available is not None and not isinstance(available, bool):
        return web.json_response(
            {"error": "available must be true, false or null"},
            status=400,
        )
    if telegram_id is not None and not isinstance(telegram_id, int):
        return web.json_response({"error": "telegram_id must be int"}, status=400)
    if price is not None and (not isinstance(price, (int, float)) or price < 0 or price > 500):
        return web.json_response({"error": "price must be 0..500"}, status=400)
    if queue_size is not None and (not isinstance(queue_size, int) or queue_size < 0 or queue_size > 100):
        return web.json_response({"error": "queue_size must be 0..100"}, status=400)

    user_id = None
    if telegram_id:
        await upsert_user(telegram_id=telegram_id, first_name=first_name)
        user_id = await get_user_id_by_telegram_id(telegram_id)

    report_id = await add_report(
        station_id=station_id,
        user_id=user_id,
        fuel_type=fuel_type,
        available=available,
        price=float(price) if price is not None else None,
        queue_size=int(queue_size) if queue_size is not None else None,
        has_limit=bool(has_limit),
        limit_liters=int(limit_liters) if limit_liters is not None else None,
        source="miniapp",
    )

    new_badges = await check_and_award_badges(user_id) if user_id else []
    return web.json_response(
        {
            "ok": True,
            "report_id": report_id,
            "new_badges": [
                {
                    "code": b,
                    "name": BADGE_CATALOG.get(b, {}).get("name"),
                    "emoji": BADGE_CATALOG.get(b, {}).get("emoji"),
                    "desc": BADGE_CATALOG.get(b, {}).get("desc"),
                }
                for b in new_badges
            ],
        }
    )


async def handle_price_update(request):
    """POST /api/price-update — обновление цены топлива (от владельца/пользователя).

    Тело: { station_id, fuel_type, price, available?, queue_size?, telegram_id? }
    Создаёт обычный отчёт с заполненным price.
    """
    if not _check_rate(request.remote or "?", 30):
        return web.json_response({"error": "rate limit exceeded"}, status=429)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    if not isinstance(data, dict):
        return web.json_response({"error": "expected json object"}, status=400)

    station_id = data.get("station_id")
    fuel_type = data.get("fuel_type")
    price = data.get("price")
    available = data.get("available", True)
    queue_size = data.get("queue_size")
    telegram_id = data.get("telegram_id")
    first_name = str(data.get("first_name", "PriceUpdate"))[:64]

    if not station_id or not isinstance(station_id, int):
        return web.json_response({"error": "station_id (int) is required"}, status=400)
    if not fuel_type or fuel_type not in ("92", "95", "98", "diesel", "100", "lpg"):
        return web.json_response({"error": f"invalid fuel_type: {fuel_type}"}, status=400)
    if price is None or not isinstance(price, (int, float)) or price < 0 or price > 500:
        return web.json_response({"error": "price is required, 0..500"}, status=400)

    user_id = None
    if telegram_id:
        await upsert_user(telegram_id=telegram_id, first_name=first_name)
        user_id = await get_user_id_by_telegram_id(telegram_id)

    report_id = await add_report(
        station_id=station_id,
        user_id=user_id,
        fuel_type=fuel_type,
        available=available if available in (True, False, None) else True,
        price=float(price),
        queue_size=int(queue_size) if isinstance(queue_size, int) else None,
        source="price_update",
    )

    new_badges = await check_and_award_badges(user_id) if user_id else []
    return web.json_response(
        {
            "ok": True,
            "report_id": report_id,
            "new_badges": [
                {
                    "code": b,
                    "name": BADGE_CATALOG.get(b, {}).get("name"),
                    "emoji": BADGE_CATALOG.get(b, {}).get("emoji"),
                    "desc": BADGE_CATALOG.get(b, {}).get("desc"),
                }
                for b in new_badges
            ],
        }
    )


# === CORS ===
# ВНИМАНИЕ: в проде ограничить через ALLOWED_ORIGINS env var.
ALLOWED_ORIGINS = "*"  # default для dev; в проде задать через env


async def cors_middleware(app, handler):
    """CORS-заголовки."""
    async def middleware(request):
        if request.method == "OPTIONS":
            return web.Response(headers={
                "Access-Control-Allow-Origin": ALLOWED_ORIGINS,
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            })
        response = await handler(request)
        response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGINS
        return response
    return middleware


def create_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/logs", handle_logs)
    app.router.add_get("/api/admin/stats", handle_admin_stats)
    app.router.add_get("/api/reverse-geocode", handle_reverse_geocode)
    app.router.add_get("/api/stations", handle_stations)
    app.router.add_get("/api/stations/by-city", handle_stations_by_city)
    app.router.add_get("/api/search", handle_search)
    app.router.add_get("/api/stations/{id}", handle_station_detail)
    app.router.add_get("/api/stations/{id}/price-history", handle_price_history)
    app.router.add_get("/api/stations/{id}/analytics", handle_station_analytics)
    app.router.add_get("/api/stations/{id}/prices", handle_station_prices)
    app.router.add_get("/api/premium-status", handle_premium_status)
    app.router.add_post("/api/reports", handle_create_report)
    app.router.add_post("/api/price-update", handle_price_update)
    return app
