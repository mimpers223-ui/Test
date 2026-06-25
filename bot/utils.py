"""
Утилиты форматирования.
"""


def format_distance(km: float) -> str:
    """Форматирует расстояние в км/м."""
    if km < 1:
        return f"{int(km * 1000)} м"
    elif km < 10:
        return f"{km:.1f} км"
    else:
        return f"{int(km)} км"


def format_fuel_status(status: dict | None) -> str:
    """Форматирует статус одного вида топлива."""
    if not status:
        return "  • ⛽ — <i>нет данных</i>"

    available = status.get("available")
    fuel = status.get("fuel_type", "?")
    price = status.get("price")
    queue = status.get("queue_size")
    has_limit = status.get("has_limit")
    limit_liters = status.get("limit_liters")
    confidence = status.get("confidence", 0)
    last_at = status.get("last_report_at")

    if available is True:
        icon = "✅"
        text = "есть"
    elif available is False:
        icon = "❌"
        text = "нет"
    else:
        icon = "⚠️"
        text = "кончается"

    line = f"  • {icon} <b>АИ-{fuel}</b>: {text}"

    if price is not None:
        line += f"  •  <b>{price:.2f}₽</b>"

    if has_limit and limit_liters:
        line += f"  •  лимит {limit_liters}л"

    if queue is not None and queue > 0:
        line += f"  •  очередь ~{queue}"

    if confidence and confidence > 0:
        if confidence >= 0.8:
            badge = "🟢"
        elif confidence >= 0.5:
            badge = "🟡"
        else:
            badge = "🔴"
        line += f"  {badge}"

    if last_at:
        line += f"\n      <i>(обновлено {format_time_ago(last_at)})</i>"

    return line


def format_time_ago(dt) -> str:
    """Время назад в человеческом формате."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return "только что"
    elif seconds < 3600:
        return f"{seconds // 60} мин назад"
    elif seconds < 86400:
        return f"{seconds // 3600} ч назад"
    else:
        return f"{seconds // 86400} дн назад"


def format_station_card(station: dict, statuses: list | None = None) -> str:
    """Форматирует карточку АЗС."""
    name = station.get("name", "АЗС")
    operator = station.get("operator")
    city = station.get("city")
    address = station.get("address")
    fuel_types = station.get("fuel_types") or []
    has_24_7 = station.get("has_24_7")
    is_verified = station.get("is_verified")

    lines = [f"⛽ <b>{name}</b>"]
    if operator:
        lines.append(f"🏢 Оператор: {operator}")
    if city:
        lines.append(f"🏙 Город: {city}")
    if address:
        lines.append(f"📍 {address}")

    extras = []
    if has_24_7:
        extras.append("24/7")
    if is_verified:
        extras.append("✓ Verified")
    if fuel_types:
        extras.append("топливо: " + ", ".join(fuel_types[:5]))
    if extras:
        lines.append(f"ℹ️ {'  •  '.join(extras)}")

    if statuses:
        lines.append("")
        lines.append("<b>Текущий статус:</b>")
        for s in statuses:
            lines.append(format_fuel_status(s))
    else:
        lines.append("")
        lines.append("<i>Нет свежих отчётов. Нажми «📝 Сообщить» чтобы добавить.</i>")

    return "\n".join(lines)
