# ⛽ Бензин рядом

Telegram-бот + Mini App для поиска АЗС в реальном времени. Карта, цены, очереди, push-уведомления. Работает на aiogram 3 + React + MapLibre.

## Что внутри

- 🤖 **Telegram-бот** с 6 командами, inline-поиском, бейджами, Premium через Stars
- 🗺 **Mini App** на React + MapLibre (карта + список + sparkline цен)
- 💎 **Premium** через Telegram Stars (149 Stars/мес) — push без cooldown + аналитика
- 🔔 **Push-уведомления** о завозе / падении цены
- 📢 **Автопубликация** в канал `@benzyn_ryadom` (топ-5 АЗС каждые 30 мин)
- 🏪 **Verified АЗС** — владельцы регистрируются и подтверждают
- ⭐ **6 бейджей** геймификации (Новичок → Эксперт → Топ региона)
- 🌐 **Inline mode** — ищи АЗС прямо в любом чате: `@benzyn_ryadom_bot 92 Иваново`
- 📊 **Аналитика владельца** — просмотры, отчёты, средняя цена
- 💰 **Цены** — отчёты с ценой + sparkline история за 30 дней

## Структура

```
.
├── bot/                       # Backend (Python + aiogram 3)
│   ├── main.py                # Entry point: API + bot + workers
│   ├── handlers.py            # /start, /find, /subscribe, inline, premium, ...
│   ├── api.py                 # aiohttp API для Mini App
│   ├── db.py                  # SQLite / PostgreSQL
│   ├── push_worker.py         # Push-уведомления
│   ├── channel_poster.py      # Автопубликация в канал
│   ├── keyboards.py           # Reply / Inline клавиатуры
│   ├── config.py              # Settings из .env
│   └── .env                   # BOT_TOKEN, CHANNEL_CHAT_ID, ...
│
├── mini-app/                  # Frontend (React + TypeScript + Vite)
│   ├── src/
│   │   ├── App.tsx            # Главный компонент
│   │   ├── api.ts             # API клиент
│   │   ├── utils.ts           # formatAge, sparkline, ...
│   │   └── index.css          # Premium стили
│   ├── public/
│   │   ├── manifest.webmanifest  # PWA
│   │   └── icon-192/512.svg     # Иконки
│   └── package.json
│
├── scripts/
│   ├── parse_osm.py           # Парсер OSM (Overpass API)
│   ├── enrich_addresses.py    # Reverse geocoding через Nominatim
│   └── parse_tg_channels.py   # Парсер TG-каналов (Telethon)
│
├── db/
│   ├── schema.sql             # PostgreSQL
│   └── schema_sqlite.sql      # SQLite
│
├── Dockerfile                 # Для Render / Railway / Fly.io
├── vercel.json                # Для Vercel (mini-app)
├── render.yaml                # Для Render (bot + API)
├── .env.example               # Шаблон переменных
├── .github/workflows/ci.yml   # GitHub Actions: тест + билд
└── requirements.txt
```

## Локальный запуск (dev)

```bash
# 1. Установить зависимости
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd mini-app && npm install && cd ..

# 2. Скопировать и заполнить .env
cp .env.example .env
# Отредактируй: BOT_TOKEN, ADMIN_USERNAMES, MINI_APP_URL

# 3. Запустить
cd bot && python main.py
# API: http://localhost:8080
# Mini App: cd ../mini-app && npm run dev (откроет на :5173 с proxy на :8080)
```

## 🚀 Деплой в продакшн

### Шаг 1: GitHub

```bash
git init
git add .
git commit -m "Initial: бензин рядом v1.0"
gh repo create benzin-ryadom --public --source=. --push
```

### Шаг 2: Mini App на Vercel

1. Зайди на https://vercel.com/new
2. Import `benzin-ryadom` репо
3. **Root Directory**: `mini-app`
4. **Framework**: Vite
5. **Environment Variables**:
   - `VITE_API_URL` = `https://benzin-api.onrender.com` (см. шаг 3)
6. Deploy → получишь URL типа `https://benzin-mini.vercel.app`

### Шаг 3: API + Bot на Render

**Вариант A: Blueprint (рекомендуется)**

1. https://dashboard.render.com/select-repo → выбери `benzin-ryadom`
2. Render увидит `render.yaml` и предложит применить
3. Задай секреты в Environment:
   - `BOT_TOKEN` (от @BotFather)
   - `ADMIN_USERNAMES` = `darkt30`
   - `MINI_APP_URL` = `https://benzin-mini.vercel.app`
   - `CHANNEL_CHAT_ID` = `-100...` (опционально)
4. Deploy → получишь URL типа `https://benzin-api.onrender.com`

**Вариант B: Web Service вручную**

1. https://dashboard.render.com/create → Web Service
2. Repo: `benzin-ryadom`
3. **Root Directory**: `bot`
4. **Build Command**: `pip install -r ../requirements.txt`
5. **Start Command**: `python main.py`
6. **Health Check Path**: `/api/health`
7. **Instance Type**: Starter ($7/мес) или Free
8. Environment variables — см. выше

**Вариант C: Docker**

1. https://dashboard.render.com/create → Web Service
2. Environment: Docker
3. Dockerfile автоматически

### Шаг 4: Подключить CHANNEL_CHAT_ID

1. Создай канал `@benzyn_ryadom` в Telegram
2. Добавь бота `@benzyn_ryadom_bot` админом с правом `post_messages`
3. Узнай chat_id канала:
   - Перешли любое сообщение из канала боту @userinfobot
   - Или через @RawDataBot
4. В Render Environment добавь `CHANNEL_CHAT_ID=-100xxxxxxxxxx`
5. Restart service

### Шаг 5: Включить inline mode

В @BotFather → `/setinline` → `@benzyn_ryadom_bot` → placeholder: `🔍 Город, сеть или тип топлива…`

## 🛠 API endpoints

```
GET  /api/health                          # liveness probe
GET  /api/stations?lat&lon&radius&fuel    # ближайшие АЗС
GET  /api/search?q=                       # поиск по названию/сети
GET  /api/stations/{id}                   # детальная карточка
GET  /api/stations/{id}/price-history?fuel=95&days=30
GET  /api/stations/{id}/analytics?days=30 # аналитика владельца
POST /api/reports                         # {station_id, fuel_type, available, price?, queue_size?}
POST /api/price-update                    # {station_id, fuel_type, price, ...}
```

Rate limit: 60 GET / 30 POST в минуту на IP.

## 🗺 Mini App

- Vercel: https://benzin-mini.vercel.app
- URL открывается через Telegram Web App кнопку в боте
- Hero header с live-индикатором
- Список АЗС с verified-бейджами, ценами, возрастом данных
- Карта на MapLibre с OSM тайлами
- Форма отчёта с ценой и очередью
- Sparkline истории цен
- Pull-to-refresh
- PWA: можно добавить на экран телефона
- Онбординг 3 экрана при первом запуске

## 💎 Premium

149 Stars / 30 дней. Даёт:
- Push-уведомления без cooldown (4ч → 0)
- Расширенная аналитика (планируется)
- Premium-бейдж в профиле
- Больше АЗС на карте (планируется)

## 📊 Метрики

После деплоя:
- **Vercel**: https://vercel.com/dashboard → benzin-mini → Analytics
- **Render**: https://dashboard.render.com → benzin-api → Metrics
- **Бот**: `/stats` в чате

## 🔐 Безопасность

- `BOT_TOKEN` хранится в Render env (не в git)
- `.env` в `.gitignore`
- Rate limit 60/30 в мин
- CORS: `ALLOWED_ORIGINS=*` (для dev) → сузить в проде до `https://benzin-mini.vercel.app`

## Лицензия

MIT
