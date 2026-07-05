#!/usr/bin/env python3
"""Чистит названия городов в БД: убирает «город», «район», «округ» и т.д.

Запуск: python3 clean_city_names.py
"""
import sqlite3
import re
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "bot", "benzin.db")


def clean_city_name(city: str) -> str:
    """Убирает мусор из названия города."""
    if not city:
        return city

    c = city.strip()

    # "Муниципальное образование «город X»" → "X"
    m = re.match(r'Муниципальное образование\s*[«"](.+?)[»"]\s*$', c)
    if m:
        c = m.group(1)

    # "Муниципальное образование город X" → "X"
    m = re.match(r'Муниципальное образование\s+город\s+(.+)', c, re.IGNORECASE)
    if m:
        c = m.group(1)

    # "город X" / "Город X" → "X"
    m = re.match(r'^(?:город|Город)\s+(.+)', c, re.IGNORECASE)
    if m:
        c = m.group(1)

    # "X район" / "X-Y район" → убираем "район" (но оставляем если это часть названия типа "Ненецкий АО")
    # НЕ убираем "район" — это может быть и названием населённого пункта

    # "Городской округ X" / "X городской округ" → "X"
    m = re.match(r'^(?:Городской округ|городской округ)\s+(.+)', c)
    if m:
        c = m.group(1)
    else:
        c = re.sub(r'\s+(?:городской округ|Городской округ)\s*$', '', c, flags=re.IGNORECASE)

    # "Муниципальный округ X" / "X муниципальный округ" → "X"
    m = re.match(r'^(?:Муниципальный округ|муниципальный округ)\s+(.+)', c)
    if m:
        c = m.group(1)
    else:
        c = re.sub(r'\s+(?:муниципальный округ|Муниципальный округ)\s*$', '', c, flags=re.IGNORECASE)

    # "X сельское поселение" → убираем
    c = re.sub(r'\s+сельское поселение\s*$', '', c, flags=re.IGNORECASE)

    # "X с/п" → убираем
    c = re.sub(r'\s+с/п\s*$', '', c, flags=re.IGNORECASE)

    # Убираем кавычки « » и " "
    c = re.sub(r'^[«"]+', '', c)
    c = re.sub(r'[»"]+$', '', c)

    # "ЗАТО X" → "X"
    m = re.match(r'^ЗАТО\s+(?:город\s+)?(.+)', c)
    if m:
        c = m.group(1)

    return c.strip()


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Находим все уникальные названия
    rows = conn.execute("SELECT DISTINCT city FROM stations WHERE city IS NOT NULL AND city != ''").fetchall()
    print(f"Всего уникальных названий: {len(rows)}")

    # Чистим
    updates = {}
    for row in rows:
        old = row["city"]
        new = clean_city_name(old)
        if old != new:
            updates[old] = new

    print(f"Нужно обновить: {len(updates)}")

    # Показываем примеры
    for old, new in sorted(updates.items())[:30]:
        print(f"  '{old}' → '{new}'")

    # Применяем
    for old, new in updates.items():
        conn.execute("UPDATE stations SET city = ? WHERE city = ?", (new, old))

    conn.commit()

    # Проверяем результат
    count = conn.execute("SELECT COUNT(DISTINCT city) FROM stations WHERE city IS NOT NULL AND city != ''").fetchone()[0]
    print(f"\nПосле чистки: {count} уникальных городов")

    # Топ-20
    rows = conn.execute("""
        SELECT city, COUNT(*) as cnt FROM stations 
        WHERE city IS NOT NULL AND city != '' 
        GROUP BY city ORDER BY cnt DESC LIMIT 20
    """).fetchall()
    print("\nТоп-20:")
    for row in rows:
        print(f"  {row[0]}: {row[1]} АЗС")

    conn.close()


if __name__ == "__main__":
    main()
