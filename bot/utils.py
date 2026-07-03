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

    Порядок:
    1. Название сети / оператор
    2. Адрес (улица, город)
    3. Всё остальное
    """
    if statuses is None:
        statuses = station.get("statuses", [])
    import json
    name = station.get("name", "АЗС")
    operator = station.get("operator")
    city = station.get("city")
    address = station.get("address")
    fuel_types_raw = station.get("fuel_types") or []
    if isinstance(fuel_types_raw, str):
        try:
            fuel_types = json.loads(fuel_types_raw)
        except (ValueError, TypeError):
            fuel_types = [fuel_types_raw]
    else:
        fuel_types = fuel_types_raw
    has_24_7 = station.get("has_24_7")
    is_verified = station.get("is_verified")

    is_demo = False
    if statuses:
        sources = {s.get("source") for s in statuses}
        is_demo = sources == {"seed_demo"} or sources == {"seed"} or sources == {None} or sources == {"seed_demo", "seed"}

    # 1. Название сети / оператор — первая строка
    display_name = operator or name
    first_line = f"⛽ <b>{display_name}</b>"
    if is_verified:
        first_line += "  ✓"
    if is_demo:
        first_line += "  <i>(демо)</i>"
    lines = [first_line]

    # Если оператор и имя разные — показываем имя мелко
    if operator and name and name != operator:
        lines.append(f"📌 {name}")

    # 2. Адрес — вторая строка, крупно
    addr_parts = []
    if city:
        addr_parts.append(city)
    if address:
        addr_parts.append(address)
    if addr_parts:
        lines.append(f"📍 <b>{', '.join(addr_parts)}</b>")
    else:
        lines.append("📍 <i>адрес не указан</i>")

    # 3. Рейтинг
    avg_rating = station.get("avg_rating")
    total_reviews = station.get("total_reviews", 0)
    if avg_rating and total_reviews > 0:
        full_stars = int(avg_rating)
        half_star = 1 if avg_rating - full_stars >= 0.5 else 0
        empty_stars = 5 - full_stars - half_star
        stars = "⭐" * full_stars + ("✨" if half_star else "") + "☆" * empty_stars
        lines.append(f"⭐ {stars} {avg_rating} ({total_reviews} отзывов)")

    # Координаты
    lat = station.get("lat")
    lon = station.get("lon")
    if lat and lon and (lat != 0 and lon != 0):
        lines.append(f"🌐 {lat:.5f}, {lon:.5f}")

    # Extras
    extras = []
    if has_24_7:
        extras.append("24/7")
    if fuel_types and isinstance(fuel_types, list):
        if all(isinstance(f, str) for f in fuel_types):
            extras.append("топливо: " + ", ".join(fuel_types[:5]))
    if extras:
        lines.append(f"ℹ️ {'  •  '.join(extras)}")

    if statuses:
        # Группируем по fuel_type: {fuel_type: [status1, status2, ...]}
        from collections import defaultdict
        by_fuel: dict[str, list] = defaultdict(list)
        for s in statuses:
            ft = s.get("fuel_type", "unknown")
            if ft == "all":
                continue  # skip "all" - it's not a real fuel type
            by_fuel[ft].append(s)

        # Сводка по наличию (лучший статус по каждому топливу)
        fuel_availability = {}
        for ft, fuel_statuses in by_fuel.items():
            avail_values = [s.get("available") for s in fuel_statuses]
            if any(v is True for v in avail_values):
                fuel_availability[ft] = True
            elif any(v is None for v in avail_values):
                fuel_availability[ft] = None
            else:
                fuel_availability[ft] = False

        available_count = sum(1 for v in fuel_availability.values() if v is True)
        ending_count = sum(1 for v in fuel_availability.values() if v is None)
        missing_count = sum(1 for v in fuel_availability.values() if v is False)
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
        for ft in sorted(by_fuel.keys()):
            fuel_statuses = by_fuel[ft]
            # Лучший статус для этого топлива
            best = None
            for s in fuel_statuses:
                if s.get("available") is True:
                    best = s
                    break
                if s.get("available") is None and best is None:
                    best = s
                if best is None:
                    best = s

            # Иконка availability
            avail = fuel_availability.get(ft)
            if avail is True:
                icon = "✅"
                avail_text = "есть"
            elif avail is None:
                icon = "⚠️"
                avail_text = "кончается"
            else:
                icon = "❌"
                avail_text = "нет"

            # Лучшая цена из всех источников
            prices_with_source = []
            for s in fuel_statuses:
                p = s.get("price")
                src = s.get("source", "")
                if p is not None:
                    prices_with_source.append((p, src))

            price_str = ""
            if prices_with_source:
                prices_with_source.sort(key=lambda x: x[0])
                best_price, best_src = prices_with_source[0]
                src_label = {
                    "fuelprice_ru": "⛽", "gdebenz": "🌐", "tg": "📡",
                    "vk": "📡", "owner": "👨‍💼", "user": "👤",
                    "seed_demo": "🧪", "seed": "🧪",
                }.get(best_src, "")
                price_str = f"  <b>{best_price:.2f}₽</b>{src_label}"
                # Если есть разные цены — показать все
                unique_prices = sorted(set(p for p, _ in prices_with_source))
                if len(unique_prices) > 1:
                    other_prices = " / ".join(f"{p:.2f}" for p in unique_prices[1:])
                    price_str += f"  <i>({other_prices})</i>"

            fuel_label = ft if ft in ("all", "cng", "lpg") else f"АИ-{ft}"
            line = f"  • {icon} <b>{fuel_label}</b>: {avail_text}{price_str}"

            # Очередь / лимит (из лучшего отчёта)
            if best:
                queue = best.get("queue_size")
                has_limit = best.get("has_limit")
                limit_liters = best.get("limit_liters")
                if has_limit and limit_liters:
                    line += f"  •  лимит {limit_liters}л"
                if queue is not None and queue > 0:
                    line += f"  •  очередь ~{queue}"

            # Время следующего завоза
            for s in fuel_statuses:
                nd = s.get("next_delivery_at")
                if nd:
                    if isinstance(nd, str):
                        try:
                            nd = datetime.fromisoformat(nd.replace(" ", "T"))
                        except ValueError:
                            continue
                    delivery_str = format_delivery_time(nd)
                    line += f"  •  🚚 {delivery_str}"
                    break

            lines.append(line)
    else:
        lines.append("")
        lines.append("<i>Нет свежих отчётов. Нажми «📝 Сообщить» чтобы добавить.</i>")

    return "\n".join(lines)
