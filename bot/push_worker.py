"""
Worker для push-уведомлений.
Раз в N секунд сканирует свежие отчёты о наличии и шлёт алерты подписчикам.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

import db
from config import settings

logger = logging.getLogger(__name__)

PUSH_INTERVAL_SEC = 300       # как часто сканировать (5 мин)
PUSH_COOLDOWN_HOURS = 4       # антиспам: не чаще раза в 4 часа на подписку
SCAN_WINDOW_MINUTES = 10      # за какой период смотрим новые отчёты


def _parse_iso(dt_str: str) -> datetime:
    try:
        return datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc) - timedelta(days=1)


def _is_on_cooldown(last_notified_at, cooldown_hours: int) -> bool:
    if not last_notified_at:
        return False
    last = _parse_iso(last_notified_at)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last) < timedelta(hours=cooldown_hours)


def _format_push(report: dict, distance_km: float) -> str:
    """Форматирует текст push-уведомления."""
    name = report.get("name") or "АЗС"
    fuel = report.get("fuel_type", "?")
    address = report.get("address") or report.get("city") or ""
    available = report.get("available")
    queue = report.get("queue_size")
    price = report.get("price")
    prev_price = report.get("prev_price")
    prev_available = report.get("prev_available")

    if available is True or available == 1:
        status_icon = "✅"
        status_text = "есть"
    elif available is None or available == 2:
        status_icon = "⚠️"
        status_text = "кончается"
    else:
        status_icon = "❌"
        status_text = "нет"

    # === Определяем тип события для push ===
    was_none_or_no = prev_available in (False, 0, None, 2)
    became_yes = available in (True, 1) and was_none_or_no
    price_dropped = (
        price is not None
        and prev_price is not None
        and float(price) < float(prev_price) - 1.0
    )
    is_first = prev_available is None

    text = f"{status_icon} <b>{name}</b>\n"
    if address:
        text += f"📍 {address}\n"

    if became_yes:
        text += f"⛽ <b>АИ-{fuel} появилось!</b>"
    elif price_dropped:
        diff = float(prev_price) - float(price)
        text += f"⛽ АИ-{fuel}: <b>{price}₽</b> (−{diff:.2f}₽)"
    elif is_first:
        text += f"⛽ АИ-{fuel}: {status_text}"
    else:
        text += f"⛽ АИ-{fuel}: {status_text}"

    if price is not None and not price_dropped:
        text += f"  •  {price}₽"
    if queue:
        text += f"  •  🕐 очередь ~{queue}"
    if distance_km and distance_km > 0:
        if distance_km < 1:
            text += f"\n📏 ~{int(distance_km * 1000)} м от тебя"
        else:
            text += f"\n📏 ~{distance_km:.1f} км от тебя"
    return text


async def push_loop(bot: Bot):
    """Главный цикл: раз в PUSH_INTERVAL_SEC сканирует и шлёт push'и."""
    logger.info("Push worker started")
    while True:
        try:
            await _push_iteration(bot)
        except Exception as e:
            logger.exception("Push iteration failed: %s", e)
        await asyncio.sleep(PUSH_INTERVAL_SEC)


async def _send_one_push(
    bot: Bot, tg_id: int, sub_id: int, uid: int, text: str
) -> tuple[bool, bool]:
    """Отправляет одно push-уведомление. Возвращает (success, blocked)."""
    try:
        await bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML")
        await db.mark_subscription_notified(sub_id)
        return True, False
    except TelegramAPIError as e:
        err_str = str(e).lower()
        if "blocked" in err_str or "deactivated" in err_str or "chat not found" in err_str:
            await db.mark_user_blocked(tg_id)
            return False, True
        logger.warning("Push to %d failed: %s", tg_id, e)
        return False, False
    except Exception as e:
        logger.exception("Unexpected push error for %d: %s", tg_id, e)
        return False, False


async def _push_iteration(bot: Bot):
    """Одна итерация: собрать отчёты → подписчиков → отправить (параллельно)."""
    if not settings.bot:
        return
    reports = await db.get_recent_fuel_reports(minutes=SCAN_WINDOW_MINUTES)
    if not reports:
        return

    logger.info("Push scan: %d fresh reports", len(reports))
    sent = 0
    blocked_marked = 0
    seen_pairs: set = set()  # (user_id, station_id) — чтобы не слать дважды за итерацию

    for r in reports:
        station_id = r.get("station_id")
        station_lat = r.get("lat")
        station_lon = r.get("lon")
        fuel_type = r.get("fuel_type")
        if not (station_id and station_lat and station_lon):
            continue

        subs = await db.get_subscribers_for_station(
            station_id=station_id,
            station_lat=station_lat,
            station_lon=station_lon,
            fuel_type=fuel_type,
        )
        # Фильтр значимости: шлём только если событие важное
        # (появилось / цена упала / первый отчёт). Иначе — кулдаун спасёт.
        available = r.get("available")
        prev_available = r.get("prev_available")
        price = r.get("price")
        prev_price = r.get("prev_price")
        became_yes = available in (True, 1) and prev_available in (False, 0, None, 2)
        price_dropped = (
            price is not None
            and prev_price is not None
            and float(price) < float(prev_price) - 1.0
        )
        is_first = prev_available is None
        if not (became_yes or price_dropped or is_first):
            continue

        # Собираем задачи для параллельной отправки
        tasks = []
        task_meta: list[tuple[int, int, int, str]] = []  # (tg_id, sub_id, uid, text)
        for sub in subs:
            uid = sub.get("user_id")
            tg_id = sub.get("telegram_id")
            sub_id = sub.get("sub_id")
            distance_km = sub.get("distance_km", 0)

            if not tg_id or not sub_id:
                continue
            if (uid, station_id) in seen_pairs:
                continue
            # Premium: без cooldown
            premium = await db.is_premium(uid) if uid else False
            cooldown_hours = 0 if premium else PUSH_COOLDOWN_HOURS
            if _is_on_cooldown(sub.get("last_notified_at"), cooldown_hours):
                continue

            text = _format_push(r, distance_km)
            task_meta.append((tg_id, sub_id, uid, text))
            seen_pairs.add((uid, station_id))

        # Параллельная отправка (Telegram limit 30 msg/s, делим на чанки)
        if not task_meta:
            continue

        # Чанки по 25 чтобы не упереться в rate limit
        CHUNK = 25
        for i in range(0, len(task_meta), CHUNK):
            chunk = task_meta[i:i + CHUNK]
            results = await asyncio.gather(
                *[_send_one_push(bot, tg, sid, u, t) for tg, sid, u, t in chunk],
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, Exception):
                    logger.warning("Push task failed: %s", r)
                elif r and r[0]:
                    sent += 1
                if r and r[1]:
                    blocked_marked += 1
            # Пауза между чанками (Telegram limit 30 msg/s)
            if i + CHUNK < len(task_meta):
                await asyncio.sleep(0.5)

    if sent or blocked_marked:
        logger.info("Push: sent %d notifications, marked %d blocked", sent, blocked_marked)
