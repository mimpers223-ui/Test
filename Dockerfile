FROM python:3.12-slim

WORKDIR /app

# Системные зависимости для aiosqlite / asyncpg / certifi
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Python зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Код приложения
COPY bot/ ./bot/
COPY db/ ./db/

# Создаём пользователя для безопасности
RUN useradd -m -u 1001 botuser \
    && chown -R botuser:botuser /app
USER botuser

WORKDIR /app/bot

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://localhost:{os.getenv(\"PORT\", 8080)}/api/health', timeout=5)" || exit 1

# Render выставляет PORT автоматически
ENV PORT=8080
EXPOSE 8080

CMD ["python", "main.py"]
