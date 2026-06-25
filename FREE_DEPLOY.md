# 🚀 Бесплатный деплой «Бензин рядом» — 0$/мес навсегда

## Стек (всё с free tier)

| Сервис | Назначение | Стоимость | Лимит free |
|---|---|---|---|
| **Vercel** | Mini App (frontend) | $0 | 100GB bandwidth |
| **Render** | API + Telegram-бот | $0 | 750 ч/мес, засыпает через 15 мин idle |
| **Supabase** | PostgreSQL база данных | $0 | 500MB, 2GB трафика |
| **UptimeRobot** | Пинг каждые 5 мин (чтобы Render не засыпал) | $0 | 50 мониторов |

**Итого: 0$/мес навсегда** ✓

---

## Шаг 1: GitHub (5 мин)

```bash
cd "/Users/artem/Desktop/code/бензин рядом"

# Создай репо через UI: https://github.com/new → "benzin-ryadom" (public)
# Затем:
git remote add origin git@github.com:YOUR_USERNAME/benzin-ryadom.git
git branch -M main
git push -u origin main
```

Альтернатива через `gh`:
```bash
gh repo create benzin-ryadom --public --source=. --push
```

---

## Шаг 2: Supabase — PostgreSQL (5 мин)

1. **https://supabase.com/dashboard** → Sign up → New project
2. Имя: `benzin` · Region: `Frankfurt` · Password: `<сгенерируй>`
3. Ждём 2-3 мин пока проект создастся
4. **Settings → Database** → Connection string → **URI** → копируй
   ```
   postgresql://postgres.xxxx:PASSWORD@aws-0-eu-central-1.pooler.supabase.com:6543/postgres
   ```
5. **SQL Editor** → New query → вставь содержимое `db/schema.sql` → Run
6. **Table Editor** — должны появиться таблицы: `stations`, `users`, `reports`, etc.

### Миграция данных из SQLite (если есть)

Локально:
```bash
pip install psycopg2-binary
export DATABASE_URL='postgresql://postgres.xxxx:PASSWORD@aws-0-eu-central-1.pooler.supabase.com:6543/postgres'
python scripts/sqlite_to_pg.py
```

Вывод:
```
  users: 3 импортировано
  stations: 26429 импортировано
  reports: 14 импортировано
  ...
✅ Миграция завершена!
```

---

## Шаг 3: Render — API + Bot (5 мин)

1. **https://dashboard.render.com/** → Sign up via GitHub
2. **New +** → **Blueprint**
3. Выбери репо `benzin-ryadom` → Render увидит `render-free.yaml`
4. Нажми **Apply**
5. Render создаст `benzin-api` Web Service
6. Открой **benzin-api → Environment** и добавь секреты:

| Key | Value |
|---|---|
| `DATABASE_URL` | (из шага 2) |
| `BOT_TOKEN` | (от @BotFather) |
| `ADMIN_USERNAMES` | `darkt30` |
| `MINI_APP_URL` | `https://benzin-mini.vercel.app` (см. шаг 5) |
| `CHANNEL_CHAT_ID` | `-100...` (опционально) |
| `USE_SQLITE` | `false` |

7. **Manual Deploy → Deploy latest commit**
8. Жди 3-5 мин → API на `https://benzin-api.onrender.com`

### Проверка
```bash
curl https://benzin-api.onrender.com/api/health
# {"status": "ok"}
```

---

## Шаг 4: UptimeRobot — не дать Render заснуть (3 мин)

Render Free засыпает через 15 мин idle. Чтобы бот работал 24/7 — пингуем health endpoint:

1. **https://uptimerobot.com/** → Sign up free
2. **+ Add New Monitor**
3. **Monitor Type**: HTTP(s)
4. **Friendly Name**: `Benzin API`
5. **URL**: `https://benzin-api.onrender.com/api/health`
6. **Monitoring Interval**: `5 minutes`
7. **Create Monitor**

UptimeRobot будет пинговать каждые 5 мин → Render не засыпает → polling работает 24/7.

**Лимит**: 50 мониторов бесплатно. Хватит на 50 ботов.

---

## Шаг 5: Vercel — Mini App (3 мин)

1. **https://vercel.com/new** → Sign up via GitHub
2. **Import** репо `benzin-ryadom`
3. **Root Directory**: `mini-app` ← важно!
4. **Framework Preset**: Vite (auto)
5. **Environment Variables**:
   - `VITE_API_URL` = `https://benzin-api.onrender.com`
6. **Deploy** → через 1-2 мин Mini App на `https://benzin-mini.vercel.app`

### Проверка
- Открой `https://benzin-mini.vercel.app` — должен загрузиться UI
- Сделай `curl https://benzin-mini.vercel.app/manifest.webmanifest` — должен вернуть JSON

---

## Шаг 6: Inline mode в @BotFather (1 мин)

1. Открой @BotFather
2. `/setinline` → выбери `@benzyn_ryadom_bot`
3. Placeholder: `🔍 Город, сеть или тип топлива…`

---

## Шаг 7: CHANNEL_CHAT_ID (опционально, 3 мин)

Если хочешь автопубликацию в канале `@benzyn_ryadom`:

1. Создай канал в Telegram
2. Добавь `@benzyn_ryadom_bot` админом с правом `Post Messages`
3. Узнай ` любое сообщение из канала в @chat_id`:
   - Перешлиuserinfobot
   - Или через https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
4. В Render env → `CHANNEL_CHAT_ID=-100xxxxxxxxxx` → Manual Deploy
5. Бот начнёт постить ТОП-5 АЗС каждые 30 мин

---

## Проверка всего стека

```bash
# 1. API health
curl https://benzin-api.onrender.com/api/health
# {"status": "ok"}

# 2. Поиск АЗС
curl "https://benzin-api.onrender.com/api/stations?lat=55.7558&lon=37.6173&radius=10"
# {"stations": [...], "count": 162}

# 3. Inline search
curl "https://benzin-api.onrender.com/api/search?q=Лукойл+Москва"
# {"stations": [...]}

# 4. Mini App
open https://benzin-mini.vercel.app
# Должен загрузиться с картой

# 5. Бот
# Открой @benzyn_ryadom_bot в Telegram → /start
# Должен прийти welcome-цепочка из 3 сообщений
```

---

## Ограничения free tier и обходные пути

| Ограничение | Решение |
|---|---|
| Render засыпает | UptimeRobot (бесплатно) |
| Render: 750 ч/мес | ~31 день × 24 ч = 744 ч, хватает впритык. Если не хватит — Vercel Functions или Fly.io |
| Supabase: 500MB | 26k АЗС + отчёты ≈ 20MB. Хватит надолго |
| Vercel: 100GB bandwidth | 100k MAU × 5 MB ≈ 500GB. Хватит на 200k юзеров |

---

## Бэкапы

**Supabase** автоматически делает бэкапы на free tier. Но рекомендую:
- **Settings → Database → Backups → Daily** (включено по умолчанию)
- Или вручную: **Database → Backups → Create backup**

**Локальный бэкап**:
```bash
# Скачать дамп с Supabase
pg_dump "postgresql://postgres.xxxx:PASSWORD@aws-0-eu-central-1.pooler.supabase.com:6543/postgres" > backup.sql
```

---

## Что делать когда перерастёшь free

| Метрика | Сейчас | План |
|---|---|---|
| **< 1000 юзеров/день** | Free stack ✓ | – |
| **1k-10k юзеров** | Render $7/мес | Supabase остаётся free |
| **> 10k юзеров** | Render $25/мес (Pro) | Supabase Pro $25/мес |
| **> 100k юзеров** | Render Pro + Supabase Pro + Cloudflare CDN | + выделенный DBA |

Но мы пока в начале. **0$/мес хватит надолго** 🚀

---

## Troubleshooting

### Бот не отвечает
- Проверь UptimeRobot пингует каждые 5 мин
- Render Dashboard → Logs → найди ошибки
- Проверь BOT_TOKEN в env

### Mini App показывает "Failed to fetch"
- VITE_API_URL должен указывать на Render URL
- CORS: `ALLOWED_ORIGINS=*` в Render env (default)
- Render Dashboard → Logs → API запросы

### API возвращает 500
- DATABASE_URL правильный? Supabase проект активен?
- Схема применена? (проверь в Table Editor)

### Push не приходит
- Webhook не используется (только long polling)
- Push worker в логах: "Push scan: N fresh reports"
- Кулдаун 4ч между push одному юзеру

---

## Чеклист

- [ ] GitHub: код запушен
- [ ] Supabase: проект создан, схема применена, данные мигрированы
- [ ] Render: Web Service создан, секреты заданы, deploy успешен
- [ ] UptimeRobot: монитор настроен
- [ ] Vercel: Mini App задеплоен, VITE_API_URL задан
- [ ] @BotFather: inline mode включён
- [ ] Канал: создан, бот админ, CHAT_ID в env
- [ ] Тест: `/start` в боте → 3 сообщения пришли
- [ ] Тест: Mini App открылся, карта с маркерами
- [ ] Тест: `@benzyn_ryadom_bot 92 Иваново` → inline results

**Готово! Бот в проде за 0$/мес** 🎉
