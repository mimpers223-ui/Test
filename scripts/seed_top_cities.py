"""
Seed-скрипт: реалистичные данные о ценах и наличии для топ-30 городов РФ.

Создаёт по 5-7 АЗС в каждом городе с ценами АИ-92/95/98/дизель.
Цены — рыночные на июнь 2026 года.
Наличие — реалистичное (60% есть, 25% кончается, 15% нет).
Для кончающегося/отсутствующего — генерируется время следующего завоза.

Идемпотентен: при повторном запуске обновляет существующие, не дублирует.
"""
import asyncio
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402


# === Топ городов РФ + областные + Крым/ДНР/ЛНР ===
# Координаты центра города
CITIES = {
    # ЦФО
    "Москва":           {"lat": 55.7558, "lon": 37.6173, "region": "Москва", "coef": 1.05},
    "Воронеж":          {"lat": 51.6606, "lon": 39.2006, "region": "Воронежская область", "coef": 0.97},
    "Белгород":         {"lat": 50.5954, "lon": 36.5873, "region": "Белгородская область", "coef": 0.97},
    "Брянск":           {"lat": 53.2521, "lon": 34.3650, "region": "Брянская область", "coef": 0.96},
    "Владимир":         {"lat": 56.1360, "lon": 40.3960, "region": "Владимирская область", "coef": 0.96},
    "Иваново":          {"lat": 56.9973, "lon": 40.9714, "region": "Ивановская область", "coef": 0.96},
    "Калуга":           {"lat": 54.5290, "lon": 36.2756, "region": "Калужская область", "coef": 0.98},
    "Кострома":         {"lat": 57.7677, "lon": 40.9264, "region": "Костромская область", "coef": 0.97},
    "Курск":            {"lat": 51.7304, "lon": 36.1926, "region": "Курская область", "coef": 0.96},
    "Липецк":           {"lat": 52.6031, "lon": 39.5708, "region": "Липецкая область", "coef": 0.96},
    "Орёл":             {"lat": 52.9673, "lon": 36.0696, "region": "Орловская область", "coef": 0.96},
    "Рязань":           {"lat": 54.6296, "lon": 39.6918, "region": "Рязанская область", "coef": 0.97},
    "Смоленск":         {"lat": 54.7826, "lon": 32.0453, "region": "Смоленская область", "coef": 0.96},
    "Тамбов":           {"lat": 52.7213, "lon": 41.4171, "region": "Тамбовская область", "coef": 0.96},
    "Тверь":            {"lat": 56.8584, "lon": 35.9176, "region": "Тверская область", "coef": 0.97},
    "Тула":             {"lat": 54.1930, "lon": 37.6173, "region": "Тульская область", "coef": 0.98},
    "Ярославль":        {"lat": 57.6261, "lon": 39.8845, "region": "Ярославская область", "coef": 0.98},
    # СЗФО
    "Санкт-Петербург":  {"lat": 59.9343, "lon": 30.3351, "region": "Санкт-Петербург", "coef": 1.04},
    "Калининград":      {"lat": 54.7065, "lon": 20.5109, "region": "Калининградская область", "coef": 1.02},
    "Архангельск":      {"lat": 64.5399, "lon": 40.5152, "region": "Архангельская область", "coef": 1.01},
    "Вологда":          {"lat": 59.2205, "lon": 39.8915, "region": "Вологодская область", "coef": 0.98},
    "Мурманск":         {"lat": 68.9585, "lon": 33.0827, "region": "Мурманская область", "coef": 1.05},
    "Псков":            {"lat": 57.8136, "lon": 28.3496, "region": "Псковская область", "coef": 0.97},
    "Сыктывкар":        {"lat": 61.6764, "lon": 50.8354, "region": "Республика Коми", "coef": 1.01},
    "Петрозаводск":     {"lat": 61.7892, "lon": 34.3468, "region": "Республика Карелия", "coef": 1.00},
    "Великий Новгород": {"lat": 58.5217, "lon": 31.2755, "region": "Новгородская область", "coef": 0.97},
    # ЮФО
    "Краснодар":        {"lat": 45.0393, "lon": 38.9872, "region": "Краснодарский край", "coef": 1.01},
    "Ростов-на-Дону":   {"lat": 47.2357, "lon": 39.7015, "region": "Ростовская область", "coef": 1.00},
    "Астрахань":        {"lat": 46.3498, "lon": 48.0408, "region": "Астраханская область", "coef": 0.99},
    "Волгоград":        {"lat": 48.7194, "lon": 44.5018, "region": "Волгоградская область", "coef": 0.97},
    "Элиста":           {"lat": 46.3077, "lon": 44.2558, "region": "Республика Калмыкия", "coef": 0.98},
    "Майкоп":           {"lat": 44.6060, "lon": 40.1091, "region": "Республика Адыгея", "coef": 0.99},
    "Сочи":             {"lat": 43.5855, "lon": 39.7231, "region": "Краснодарский край", "coef": 1.02},
    # СКФО
    "Ставрополь":       {"lat": 45.0428, "lon": 41.9734, "region": "Ставропольский край", "coef": 0.98},
    "Пятигорск":        {"lat": 44.0454, "lon": 43.0543, "region": "Ставропольский край", "coef": 0.98},
    "Нальчик":          {"lat": 43.4846, "lon": 43.6072, "region": "Кабардино-Балкария", "coef": 0.97},
    "Владикавказ":      {"lat": 43.0205, "lon": 44.6819, "region": "Северная Осетия", "coef": 0.97},
    "Грозный":          {"lat": 43.3125, "lon": 45.6989, "region": "Чеченская Республика", "coef": 0.96},
    "Махачкала":        {"lat": 42.9849, "lon": 47.5047, "region": "Республика Дагестан", "coef": 0.96},
    "Магас":            {"lat": 43.2264, "lon": 44.7636, "region": "Республика Ингушетия", "coef": 0.96},
    "Черкесск":         {"lat": 44.2269, "lon": 42.0466, "region": "Карачаево-Черкесия", "coef": 0.97},
    # ПФО
    "Казань":           {"lat": 55.8304, "lon": 49.0661, "region": "Татарстан", "coef": 0.97},
    "Уфа":              {"lat": 54.7388, "lon": 55.9721, "region": "Башкортостан", "coef": 0.95},
    "Самара":           {"lat": 53.1959, "lon": 50.1002, "region": "Самарская область", "coef": 0.97},
    "Нижний Новгород":  {"lat": 56.3267, "lon": 44.0060, "region": "Нижегородская область", "coef": 0.99},
    "Оренбург":         {"lat": 51.7681, "lon": 55.0968, "region": "Оренбургская область", "coef": 0.96},
    "Пенза":            {"lat": 53.1956, "lon": 45.0114, "region": "Пензенская область", "coef": 0.96},
    "Пермь":            {"lat": 58.0105, "lon": 56.2502, "region": "Пермский край", "coef": 0.96},
    "Киров":            {"lat": 58.6035, "lon": 49.6680, "region": "Кировская область", "coef": 0.96},
    "Чебоксары":        {"lat": 56.1322, "lon": 47.2519, "region": "Чувашская Республика", "coef": 0.96},
    "Ижевск":           {"lat": 56.8389, "lon": 53.1895, "region": "Удмуртия", "coef": 0.95},
    "Саранск":          {"lat": 54.1874, "lon": 45.1834, "region": "Республика Мордовия", "coef": 0.96},
    "Ульяновск":        {"lat": 54.3029, "lon": 48.3917, "region": "Ульяновская область", "coef": 0.96},
    "Тольятти":         {"lat": 53.5078, "lon": 49.4042, "region": "Самарская область", "coef": 0.96},
    # УФО
    "Екатеринбург":     {"lat": 56.8389, "lon": 60.6057, "region": "Свердловская область", "coef": 0.98},
    "Челябинск":        {"lat": 55.1644, "lon": 61.4368, "region": "Челябинская область", "coef": 0.95},
    "Тюмень":           {"lat": 57.1522, "lon": 65.5272, "region": "Тюменская область", "coef": 0.99},
    "Курган":           {"lat": 55.4408, "lon": 65.3440, "region": "Курганская область", "coef": 0.96},
    "Сургут":           {"lat": 61.2500, "lon": 73.3864, "region": "ХМАО", "coef": 1.00},
    # СФО
    "Новосибирск":      {"lat": 55.0084, "lon": 82.9357, "region": "Новосибирская область", "coef": 0.97},
    "Омск":             {"lat": 54.9885, "lon": 73.3242, "region": "Омская область", "coef": 0.96},
    "Красноярск":       {"lat": 56.0106, "lon": 92.8525, "region": "Красноярский край", "coef": 0.99},
    "Барнаул":          {"lat": 53.3548, "lon": 83.7697, "region": "Алтайский край", "coef": 0.96},
    "Кемерово":         {"lat": 55.3540, "lon": 86.0873, "region": "Кемеровская область", "coef": 0.95},
    "Новокузнецк":      {"lat": 53.7575, "lon": 87.1924, "region": "Кемеровская область", "coef": 0.95},
    "Томск":            {"lat": 56.5010, "lon": 84.9924, "region": "Томская область", "coef": 0.98},
    "Иркутск":          {"lat": 52.2864, "lon": 104.3057, "region": "Иркутская область", "coef": 1.02},
    "Абакан":           {"lat": 53.7156, "lon": 91.4272, "region": "Республика Хакасия", "coef": 0.97},
    # ДФО
    "Хабаровск":        {"lat": 48.4802, "lon": 135.0719, "region": "Хабаровский край", "coef": 1.06},
    "Владивосток":      {"lat": 43.1198, "lon": 131.8869, "region": "Приморский край", "coef": 1.07},
    "Якутск":           {"lat": 62.0355, "lon": 129.6755, "region": "Республика Саха", "coef": 1.10},
    "Благовещенск":     {"lat": 50.2564, "lon": 127.5353, "region": "Амурская область", "coef": 1.04},
    "Чита":             {"lat": 52.0316, "lon": 113.4644, "region": "Забайкальский край", "coef": 1.03},
    "Петропавловск-Камчатский": {"lat": 53.0138, "lon": 158.6484, "region": "Камчатский край", "coef": 1.12},
    "Южно-Сахалинск":   {"lat": 46.9584, "lon": 142.7381, "region": "Сахалинская область", "coef": 1.08},
    "Магадан":          {"lat": 59.5683, "lon": 150.8009, "region": "Магаданская область", "coef": 1.12},
    # КРЫМ
    "Симферополь":      {"lat": 44.9521, "lon": 34.1024, "region": "Республика Крым", "coef": 1.00},
    "Севастополь":      {"lat": 44.6167, "lon": 33.5254, "region": "Севастополь", "coef": 1.00},
    "Керчь":            {"lat": 45.3528, "lon": 36.4744, "region": "Республика Крым", "coef": 1.00},
    "Ялта":             {"lat": 44.4849, "lon": 34.1659, "region": "Республика Крым", "coef": 1.02},
    "Евпатория":        {"lat": 45.1903, "lon": 33.3670, "region": "Республика Крым", "coef": 1.00},
    "Феодосия":         {"lat": 45.0368, "lon": 35.3800, "region": "Республика Крым", "coef": 1.00},
    # ДНР
    "Донецк":           {"lat": 48.0028, "lon": 37.8053, "region": "ДНР", "coef": 0.95},
    "Мариуполь":        {"lat": 47.0958, "lon": 37.5461, "region": "ДНР", "coef": 0.95},
    "Горловка":         {"lat": 48.2997, "lon": 38.0438, "region": "ДНР", "coef": 0.95},
    "Краматорск":       {"lat": 48.7318, "lon": 37.5553, "region": "ДНР", "coef": 0.95},
    "Славянск":         {"lat": 48.8618, "lon": 37.6059, "region": "ДНР", "coef": 0.95},
    # ЛНР
    "Луганск":          {"lat": 48.5740, "lon": 39.3078, "region": "ЛНР", "coef": 0.95},
    "Алчевск":          {"lat": 48.4684, "lon": 38.8058, "region": "ЛНР", "coef": 0.95},
    "Лисичанск":        {"lat": 48.9115, "lon": 38.4924, "region": "ЛНР", "coef": 0.95},
    "Северодонецк":     {"lat": 48.9490, "lon": 38.4838, "region": "ЛНР", "coef": 0.95},
}


# Сетевые операторы (с привязкой к типам АЗС)
NETWORKS = [
    {"name": "Лукойл",        "weight": 15},
    {"name": "Газпромнефть",  "weight": 15},
    {"name": "Роснефть",      "weight": 14},
    {"name": "Татнефть",      "weight": 8},
    {"name": "Башнефть",      "weight": 6},
    {"name": "Нефтьмагистраль", "weight": 4},
    {"name": "ИП Хусаинов",   "weight": 3},
    {"name": "АЗС №1",        "weight": 3},
    {"name": "Опти",          "weight": 2},
    {"name": "Ивойл",         "weight": 2},
    {"name": "Газойл",        "weight": 2},
    {"name": "Петрол",        "weight": 2},
    {"name": "Автозаправка",  "weight": 2},
    {"name": "Сибирь",        "weight": 2},
    {"name": "АГНКС",         "weight": 1},
]

# Базовые цены (Москва, июнь 2026)
BASE_PRICES = {
    "92":     60.50,
    "95":     65.20,
    "98":     78.40,
    "100":    85.50,
    "diesel": 73.10,
    "lpg":    32.40,
}


def gen_availability() -> tuple[bool | None, datetime | None]:
    """Генерирует реалистичное наличие топлива и время следующего завоза.
    
    Распределение (на июнь 2026 в РФ):
    - 60% — есть в наличии (next_delivery = None)
    - 25% — кончается, завоз через 1-8 часов
    - 15% — нет в наличии, завоз через 4-24 часа
    """
    r = random.random()
    if r < 0.60:
        return True, None
    elif r < 0.85:
        # Кончается — завоз через 1-8 часов
        hours = random.randint(1, 8)
        return None, datetime.now() + timedelta(hours=hours)
    else:
        # Нет — завоз через 4-24 часа
        hours = random.randint(4, 24)
        return False, datetime.now() + timedelta(hours=hours)


def generate_stations(city: str, info: dict, n: int = 6) -> list[dict]:
    """Генерирует список АЗС с реалистичными ценами и наличием."""
    stations = []
    operators = [n["name"] for n in NETWORKS]
    weights = [n["weight"] for n in NETWORKS]

    coef = info["coef"]
    for i in range(n):
        op = random.choices(operators, weights=weights)[0]
        op_offset = random.uniform(-0.5, 0.5)

        # Какие виды топлива есть на этой АЗС
        has_fuel = {
            "92":     True,
            "95":     True,
            "98":     random.random() < 0.7,
            "100":    random.random() < 0.2,
            "diesel": random.random() < 0.85,
            "lpg":    random.random() < 0.4,
        }

        fuel_data = {}
        for fuel in ["92", "95", "98", "100", "diesel", "lpg"]:
            if not has_fuel[fuel]:
                fuel_data[fuel] = (None, None, None)  # нет такого вида топлива
                continue

            # Реалистичное наличие + время завоза
            available, next_delivery = gen_availability()

            # Цена
            base = BASE_PRICES[fuel] * coef + op_offset
            price = round(base + random.uniform(-0.3, 0.3), 2)

            fuel_data[fuel] = (available, price, next_delivery)

        # Очередь (только если есть 92-й)
        queue = random.choices([0, 1, 2, 3, 5, 8, 12], weights=[40, 25, 15, 10, 5, 3, 2])[0]

        # Координаты
        lat = info["lat"] + random.uniform(-0.05, 0.05)
        lon = info["lon"] + random.uniform(-0.05, 0.05)

        station = {
            "name": f"{op} №{random.randint(100, 999)}" if op not in ("АЗС №1", "Автозаправка") else f"{op} {city}",
            "operator": op,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "address": f"{city}, ул. {random.choice(['Ленина', 'Мира', 'Гагарина', 'Советская', 'Молодёжная', 'Пушкина', 'Кирова', 'Чехова', 'Горького'])}, {random.randint(1, 150)}",
            "city": city,
            "region": info["region"],
            "fuel_data": fuel_data,
            "queue_size": queue,
        }
        stations.append(station)
    return stations


async def main():
    random.seed(42)  # детерминированный seed для воспроизводимости

    print(f"=== Seed: реалистичные цены/наличие для {len(CITIES)} городов ===")
    print(f"    Наличие: 60% есть / 25% кончается / 15% нет")
    print(f"    Время завоза: 1-8ч (кончается) / 4-24ч (нет)")
    print()
    await db.init_db()

    total_stations = 0
    total_reports = 0
    total_with_delivery = 0

    for city, info in CITIES.items():
        stations = generate_stations(city, info, n=6)
        city_stations = 0
        city_reports = 0
        print(f"  [{city}] starting...", flush=True)

        for st in stations:
            # Ищем существующую АЗС по координатам (~100м)
            existing = await db._fetch(
                """SELECT id FROM stations
                   WHERE ABS(lat - ?) < 0.001 AND ABS(lon - ?) < 0.001
                   LIMIT 1""",
                st["lat"], st["lon"],
            )
            if existing:
                station_id = existing[0]["id"]
                await db._execute(
                    """UPDATE stations
                       SET operator = COALESCE(NULLIF(?, ''), operator),
                           name = COALESCE(NULLIF(?, ''), name),
                           address = COALESCE(NULLIF(?, ''), address)
                       WHERE id = ?""",
                    st["operator"], st["name"], st["address"], station_id,
                )
            else:
                station_id = await db._execute(
                    """INSERT INTO stations (name, operator, lat, lon, address, city, region,
                                             fuel_types, is_verified, is_active)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1)""",
                    st["name"], st["operator"], st["lat"], st["lon"],
                    st["address"], st["city"], st["region"], '["92","95","98","diesel"]',
                    returning=True,
                )
                city_stations += 1

                # Создаём отчёты
            for fuel, (available, price, next_delivery) in st["fuel_data"].items():
                if available is None and price is None and next_delivery is None:
                    continue
                report_id = await db.add_report(
                    station_id=station_id,
                    fuel_type=fuel,
                    available=available,
                    price=price,
                    queue_size=st["queue_size"] if fuel == "92" else None,
                    source="seed_demo",  # помечаем как демо-данные
                    comment=f"seed_demo: {city}",
                    next_delivery_at=next_delivery,
                )
                if next_delivery is not None:
                    total_with_delivery += 1
                city_reports += 1
                total_reports += 1

        total_stations += city_stations
        print(f"  [{city}] +{city_stations} АЗС, {city_reports} отчётов", flush=True)

    print()
    print(f"=== Итого ===")
    print(f"  Новых АЗС: {total_stations}")
    print(f"  Отчётов: {total_reports}")
    print(f"  С временем завоза: {total_with_delivery}")
    print(f"  Городов: {len(CITIES)}")

    print()
    print("=== Топ-5 городов по АЗС в БД ===")
    top = await db._fetch(
        """SELECT city, COUNT(*) as c FROM stations
           WHERE city IS NOT NULL AND city != ''
           GROUP BY city ORDER BY c DESC LIMIT 5"""
    )
    for r in top:
        print(f"  {r['city']}: {r['c']}")

    await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
