#!/bin/bash
# VPS Cron: запуск всех парсеров каждый час
# Установка: 0 * * * * /opt/benzin-ryadom/scripts/run_all_parsers.sh >> /tmp/parsers.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Activate venv
if [ -d "$PROJECT_DIR/.venv" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
elif [ -d "$PROJECT_DIR/venv" ]; then
    source "$PROJECT_DIR/venv/bin/activate"
fi

cd "$PROJECT_DIR"
export PYTHONPATH="$PROJECT_DIR"

# Load env
if [ -f "$PROJECT_DIR/bot/.env" ]; then
    set -a
    source "$PROJECT_DIR/bot/.env"
    set +a
fi

echo ""
echo "$(date '+%Y-%m-%d %H:%M:%S') === Starting all parsers ==="

# 1. fuelprice.ru (цены, 60+ городов)
echo "$(date '+%Y-%m-%d %H:%M:%S') fuelprice..."
python scripts/parse_fuelprice.py 2>&1 | tail -5 || echo "fuelprice FAILED"

# 2. gdebenz.ru (наличие, 40+ городов)
echo "$(date '+%Y-%m-%d %H:%M:%S') gdebenz..."
python scripts/parse_gdebenz_fast.py 2>&1 | tail -5 || echo "gdebenz FAILED"

# 3. ishubenzin.ru (наличие, crowd-sourced)
echo "$(date '+%Y-%m-%d %H:%M:%S') ishubenzin..."
python scripts/parse_ishubenzin.py 2>&1 | tail -5 || echo "ishubenzin FAILED"

# 4. Telegram channels — ТОЛЬКО если TG_API заданы и Telegram доступен
if [ -n "$TG_API_ID" ] && [ -n "$TG_API_HASH" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') tg channels..."
    timeout 60 python scripts/parse_tg_channels.py 2>&1 | tail -5 || echo "  ⏭ tg channels: skipped (timeout or blocked)"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') tg channels: skipped (no API keys)"
fi

# 5. benzin_status_bot (user-facing бот) — требует session string
if [ -n "$TG_SESSION_STRING" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') benzin_status_bot..."
    timeout 120 python scripts/parse_benzin_status_bot.py 2>&1 | tail -5 || echo "  ⏭ benzin_status_bot: skipped"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') benzin_status_bot: skipped (no session string)"
fi

# 6. Seed data refresh
echo "$(date '+%Y-%m-%d %H:%M:%S') seed demo..."
python scripts/seed_top_cities.py 2>&1 | tail -3 || echo "seed FAILED"

echo "$(date '+%Y-%m-%d %H:%M:%S') === All parsers finished ==="
