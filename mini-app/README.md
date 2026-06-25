# =====================================================
# «Бензин рядом» — Mini App
# Telegram Web App с картой АЗС
# =====================================================

## Структура

```
mini-app/
├── src/
│   ├── App.tsx       — главный компонент
│   ├── api.ts        — HTTP клиент к бэкенду
│   ├── types.ts      — TypeScript типы
│   ├── utils.ts      — хелперы
│   ├── index.css     — стили
│   └── main.tsx      — точка входа
├── index.html
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── tsconfig.json
└── vercel.json       — конфиг для Vercel
```

## Локальная разработка

```bash
cd mini-app
npm install
npm run dev
# Открой http://localhost:5173
```

Бот должен быть запущен (на порту 8080) — Vite проксирует /api на бот.

## Деплой на Vercel

1. Зайди на https://vercel.com → Sign up через GitHub
2. New Project → Import этот репо
3. **Root Directory:** `mini-app`
4. **Framework Preset:** Vite (определится автоматически)
5. **Build Command:** `npm run build`
6. **Output Directory:** `dist`
7. Добавь переменную окружения:
   - Key: `VITE_API_URL`
   - Value: URL твоего бота (например `https://benzin-bot.onrender.com`)
8. Deploy

## Настройка кнопки меню в боте

После деплоя:
1. Открой @BotFather
2. `/setmenubutton` → выбери бота
3. Укажи URL Mini App: `https://benzin-mini.vercel.app`
4. Готово — в боте появится кнопка «Открыть»

## Environment Variables

| Переменная | Когда | Описание |
|---|---|---|
| `VITE_API_URL` | Production | URL бэкенда (где крутится бот) |

Без неё Mini App будет ходить на относительный `/api` (только в dev).
