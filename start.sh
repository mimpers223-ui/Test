#!/usr/bin/env bash
# Стартовый скрипт для Render / любого PaaS
set -e

# Загружаем .env если есть
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

# Запускаем бот + API
cd bot
exec python main.py
