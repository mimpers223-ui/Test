# VK Mini App — настройка

## Регистрация

1. Откройте сообщество: https://vk.com/benzyn_ryadom
2. Управление → Настройки → Работа с API → Mini Apps
3. Создать приложение:
   - Название: **Бензин рядом**
   - URL: `https://benzin-ryadom.onrender.com/v2`
   - Платформы: Web (включить)
4. Получите `app_id` (числовой ID)
5. Включите приложение в сообществе

## Конфигурация

В Render → Environment Variables добавьте:

```
VK_MINI_APP_ID=123456789
```

(где `123456789` — ваш реальный app_id)

После перезапуска бота в `/start` появится кнопка **📱 Открыть приложение**.

## Что было сделано

### Frontend (`miniapp/`)
- ✅ `manifest.webmanifest` — для VK Mini App
- ✅ Иконки 192x192, 512x512 + favicon
- ✅ VK-специфичные мета-теги (`vk-color-scheme`, `og:*`)
- ✅ VK Bridge инициализация: `VKWebAppInit`, `VKWebAppGetLaunchParams`
- ✅ Тема VK: `vkontakte_dark` / `bright_light` (через CSS-классы `vk-dark` / `vk-light`)
- ✅ VK haptic: `VKWebAppTapticImpactOccurred`, `VKWebAppTapticNotificationOccurred`
- ✅ VK close/expand: `VKWebAppClose`, `VKWebAppExpand`
- ✅ Отправка `X-VK-User-Id` header в API для premium detection

### Backend (`bot/`)
- ✅ Кнопка `open_app` в главном меню VK (если задан `VK_MINI_APP_ID`)
- ✅ Premium detection по `X-VK-User-Id` header
- ✅ VK user_id используется вместо telegram_id для авторизации

## Как работает в VK

1. Пользователь нажимает **📱 Открыть приложение** в боте
2. VK открывает Mini App в webview по URL `https://vk.com/app{app_id}`
3. Mini App загружается с `https://benzin-ryadom.onrender.com/v2`
4. VK Bridge передаёт `launch_params` (vk_user_id, scheme, sign)
5. App адаптируется под тему VK (тёмная/светлая)
6. Все API-запросы включают `X-VK-User-Id` для premium detection
7. Пользователь видит тот же UI что в TG, но в стиле VK

## Деплой

После регистрации:
1. Деплой уже включён (URL `/v2` доступен)
2. Добавьте `VK_MINI_APP_ID` в Render environment
3. Перезапустите сервис
4. Проверьте: откройте бот → /start → должна появиться кнопка
