# Оставшиеся проблемы и планы

## Оставшиеся баги (из аудита)

### СРЕДНИЕ
1. **VK _user_state race condition** — dict без блокировок между async read/write
2. **VK memory leak** — `_user_state`, `_owner_waiting_*`, `_cache` растут без TTL
3. **api.py CORS** — `ALLOWED_ORIGINS = "*"` нужен env var
4. **handlers.py monkey-patching** — `Message.answer` модифицируется глобально
5. **handlers.py bot-to-bot loop** — `F.from_user.is_bot` ловит любого бота
6. **db.py sync sqlite3** — `_import_from_sqlite_pg` блокирует event loop
7. **db.py bare except** — `except:` ловит SystemExit
8. **db.py executescript** — не атомарный, может применить схему частично
9. **push_worker N+1** — `is_premium()` вызывается per subscriber
10. **channel_poster created_at_timestamp** — несуществующий ключ, сортировка случайная
11. **api.py handle_logs** — весь лог в память, может OOM
12. **api.py has_limit** — `bool(None)` = False, теряется None
13. **utils.py format_time_ago** — возвращает raw строку при ошибке парсинга

### НИЗКИЕ
14. **db.py import re** — на каждый вызов _fetch/_execute
15. **db.py redundant imports** — datetime импортируется локально
16. **keyboards.py unused city param** — в report_station_keyboard
17. **messages.py missing space** — "—выбери" → "— выбери"
18. **config.py type hints** — `list = None` → `list | None = None`

## Планы по развитию

### Монетизация
- Партнёрские программы (ОСАГО, автозапчасти)
- CPA-сети (Admitad, CityAds)
- Пока рано — 1 пользователь

### Раскрутка
- 7-дневный контент-план создан в папке `посты/`
- Визуалы: 8 статичных (1080×1080) + 5 анимированных шортсов
- SEO: BotFather описание, VK community SEO
- Рекламный пост с причинами выбора

### Техническое
- Добавить больше TG каналов для конкретных городов
- Рассмотреть VK Mini App
- Push-уведомления улучшить (сейчас N+1 query)
- Mini App: проверить работу поиска после фикса API (500 → исправлен params в PG)
- Mini App: версионирование localStorage для автоперезагрузки
