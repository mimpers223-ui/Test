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
    """Форматирует статус одного вида топлива.

    Показывает: иконку наличия, цену, очередь, время следующего завоза (если известно),
    и confidence (🟢/🟡/🔴), источник данных.
    """
    if not status:
        return "  • ⛽ — <i>нет данных</i>"

    available = status.get("available")
    fuel = status.get("fuel_type", "?")
    price = status.get("price")
    queue = status.get("queue_size")
    has_limit = status.get("has_limit")
    limit_liters = status.get("limit_liters")
    confidence = status.get("confidence", 0)
    last_at = status.get("last_report_at") or status.get("created_at")
    next_delivery = status.get("next_delivery_at")
    source = status.get("source")

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

    # Время следующего завоза — только если известно
    if next_delivery:
        delivery_str = format_delivery_time(next_delivery)
        line += f"  •  🚚 {delivery_str}"

    if confidence and confidence > 0:
        if confidence >= 0.8:
            badge = "🟢"
        elif confidence >= 0.5:
            badge = "🟡"
        else:
            badge = "🔴"
        line += f"  {badge}"

    # Источник данных
    source_emoji = {
        "seed_demo": "🧪демо",  # демо-данные (сгенерированы)
        "seed": "🧪",
        "user": "👤",
        "owner": "👨‍💼",
        "fuelprice_ru": "⛽",
        "tg": "📡",
        "vk": "📡",
        "max": "📡",
        "benzin_price_ru": "🌐",
        "price_update": "✏️",
    }.get(source, "")
    if source_emoji and source:
        line += f"  {source_emoji}"

    if last_at:
        line += f"\n      <i>(обновлено {format_time_ago(last_at)})</i>"

    return line


def format_delivery_time(dt) -> str:
    """Форматирует время следующего завоза в человеческом формате.

    dt: datetime (UTC или naive) или ISO-строка.
    Возвращает: "через 2ч 30м" / "завтра в 04:50" / "01.07 в 12:00"
    """
    from datetime import datetime, timezone, timedelta
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace(" ", "T"))
        except ValueError:
            return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    delta = dt - now
    seconds = int(delta.total_seconds())

    if seconds <= 0:
        return "уже должен быть"
    if seconds < 3600:
        return f"через {seconds // 60} мин"
    if seconds < 8 * 3600:
        # Меньше 8 часов — показываем "через Xч Yм"
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"через {h}ч {m}м" if m else f"через {h}ч"
    # Больше 8 часов — показываем день и время (по локальному времени UTC+3)
    local_tz = timezone(timedelta(hours=3))
    local_dt = dt.astimezone(local_tz)
    local_now = now.astimezone(local_tz)

    if local_dt.date() == local_now.date():
        return f"сегодня в {local_dt.strftime('%H:%M')}"
    if local_dt.date() == (local_now + timedelta(days=1)).date():
        return f"завтра в {local_dt.strftime('%H:%M')}"
    if local_dt.date() == (local_now + timedelta(days=2)).date():
        return f"послезавтра в {local_dt.strftime('%H:%M')}"
    return f"{local_dt.strftime('%d.%m')} в {local_dt.strftime('%H:%M')}"


def format_time_ago(dt) -> str:
    """Время назад в человеческом формате.

    dt: datetime или ISO-строка.
    """
    from datetime import datetime, timezone, timedelta
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace(" ", "T"))
        except ValueError:
            return dt
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
    """Форматирует карточку АЗС.

    Показывает:
    - Название, оператор, город, адрес
    - Сводку по наличию (✅ N / ⚠ N / ❌ N)
    - Ближайший завоз (если есть в отчётах)
    - Детали по каждому виду топлива
    - Пометку [ДЕМО] если все данные из seed
    """
    import json
    name = station.get("name", "АЗС")
    operator = station.get("operator")
    city = station.get("city")
    address = station.get("address")
    fuel_types_raw = station.get("fuel_types") or []
    # Парсим fuel_types если это JSON-строка
    if isinstance(fuel_types_raw, str):
        try:
            fuel_types = json.loads(fuel_types_raw)
        except (ValueError, TypeError):
            fuel_types = [fuel_types_raw]
    else:
        fuel_types = fuel_types_raw
    has_24_7 = station.get("has_24_7")
    is_verified = station.get("is_verified")

    # Определяем, все ли отчёты из seed
    is_demo = False
    if statuses:
        sources = {s.get("source") for s in statuses}
        is_demo = sources == {"seed_demo"} or sources == {"seed"} or sources == {None} or sources == {"seed_demo", "seed"}

    lines = [f"⛽ <b>{name}</b>"]
    if is_demo:
        lines[0] += "  <i>(демо-данные)</i>"
    if is_verified:
        lines[0] += "  ✓"
    if operator:
        lines.append(f"🏢 {operator}")

    # Адрес — крупно и заметно
    addr_parts = []
    if city:
        addr_parts.append(city)
    if address:
        addr_parts.append(address)
    if addr_parts:
        lines.append(f"📍 <b>{', '.join(addr_parts)}</b>")

    # Координаты (мелко, для справки)
    lat = station.get("lat")
    lon = station.get("lon")
    if lat and lon and (lat != 0 and lon != 0):
        lines.append(f"🌐 {lat:.5f}, {lon:.5f}")

    extras = []
    if has_24_7:
        extras.append("24/7")
    if fuel_types and isinstance(fuel_types, list):
        if all(isinstance(f, str) for f in fuel_types):
            extras.append("топливо: " + ", ".join(fuel_types[:5]))
    if extras:
        lines.append(f"ℹ️ {'  •  '.join(extras)}")

    if statuses:
        # Сводка по наличию
        available_count = sum(1 for s in statuses if s.get("available") is True)
        ending_count = sum(1 for s in statuses if s.get("available") is None)
        missing_count = sum(1 for s in statuses if s.get("available") is False)
        summary_parts = []
        if available_count:
            summary_parts.append(f"✅ {available_count}")
        if ending_count:
            summary_parts.append(f"⚠️ {ending_count}")
        if missing_count:
            summary_parts.append(f"❌ {missing_count}")
        if summary_parts:
            lines.append(f"\n<b>Наличие:</b> {'  '.join(summary_parts)}")

        # Ближайший завоз
        from datetime import datetime, timezone
        deliveries = []
        for s in statuses:
            nd = s.get("next_delivery_at")
            if nd:
                if isinstance(nd, str):
                    try:
                        nd = datetime.fromisoformat(nd.replace(" ", "T"))
                    except ValueError:
                        continue
                deliveries.append((nd, s.get("fuel_type")))
        if deliveries:
            deliveries.sort(key=lambda x: x[0])
            nd, fuel = deliveries[0]
            soon_str = format_delivery_time(nd)
            lines.append(f"🚚 <b>Ближайший завоз:</b> АИ-{fuel} {soon_str}")

        lines.append("")
        lines.append("<b>По видам топлива:</b>")
        for s in statuses:
            lines.append(format_fuel_status(s))
    else:
        lines.append("")
        lines.append("<i>Нет свежих отчётов. Нажми «📝 Сообщить» чтобы добавить.</i>")

    return "\n".join(lines)
