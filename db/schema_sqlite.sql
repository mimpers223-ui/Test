-- =====================================================
-- «Бензин рядом» — схема БД (SQLite версия для локальной разработки)
-- Когда SSL к Supabase заработает — мигрируем на PostgreSQL
-- =====================================================

-- АЗС
CREATE TABLE IF NOT EXISTS stations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    osm_id INTEGER UNIQUE,
    name TEXT NOT NULL,
    operator TEXT,
    brand TEXT,
    network TEXT,
    country TEXT DEFAULT 'RU',
    region TEXT,
    city TEXT,
    address TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    fuel_types TEXT,
    has_24_7 INTEGER DEFAULT 0,
    phone TEXT,
    website TEXT,
    is_verified INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_stations_geo ON stations (lat, lon);
CREATE INDEX IF NOT EXISTS idx_stations_operator ON stations (operator);
CREATE INDEX IF NOT EXISTS idx_stations_region ON stations (region);
CREATE INDEX IF NOT EXISTS idx_stations_city ON stations (city);

-- Пользователи
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    language_code TEXT DEFAULT 'ru',
    reputation INTEGER DEFAULT 50,
    total_reports INTEGER DEFAULT 0,
    confirmed_reports INTEGER DEFAULT 0,
    badge TEXT,
    region TEXT,
    city TEXT,
    is_owner INTEGER DEFAULT 0,
    is_blocked INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_active_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users (telegram_id);

-- Отчёты о наличии топлива
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    fuel_type TEXT NOT NULL,
    available INTEGER NOT NULL,
    price REAL,
    queue_size INTEGER,
    has_limit INTEGER DEFAULT 0,
    limit_liters INTEGER,
    comment TEXT,
    confidence REAL DEFAULT 0.5,
    confirmations INTEGER DEFAULT 0,
    disputes INTEGER DEFAULT 0,
    source TEXT DEFAULT 'user',
    expires_at TEXT,
    next_delivery_at TEXT,                       -- прогноз следующего завоза (если известен)
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_reports_station ON reports (station_id, fuel_type);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports (created_at DESC);

-- Отзывы о качестве бензина
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    fuel_type TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK (rating >= 0 AND rating <= 5),
    comment TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_reviews_station ON reviews (station_id, fuel_type);
CREATE INDEX IF NOT EXISTS idx_reviews_created ON reviews (created_at DESC);

-- Подписки на уведомления
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_id INTEGER REFERENCES stations(id) ON DELETE CASCADE,
    city TEXT,
    region TEXT,
    fuel_type TEXT NOT NULL DEFAULT '92',
    radius_km INTEGER DEFAULT 5,
    center_lat REAL,
    center_lon REAL,
    is_active INTEGER DEFAULT 1,
    last_notified_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    -- Один пользователь — одна подписка на конкретную АЗС (защита от дублей)
    UNIQUE(user_id, station_id)
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions (user_id) WHERE is_active = 1;

-- Заявки владельцев
CREATE TABLE IF NOT EXISTS owner_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    inn TEXT,
    license_photo_url TEXT,
    sign_photo_url TEXT,
    status TEXT DEFAULT 'pending',
    moderator_id INTEGER REFERENCES users(id),
    rejection_reason TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    approved_at TEXT
);

-- Связь владелец ↔ АЗС (после одобрения)
CREATE TABLE IF NOT EXISTS owner_stations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    inn TEXT,
    role TEXT DEFAULT 'owner',              -- 'owner' / 'employee'
    is_verified INTEGER DEFAULT 0,           -- одобрено модератором
    moderator_id INTEGER REFERENCES users(id),
    rejection_reason TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    verified_at TEXT,
    UNIQUE(user_id, station_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_stations_user
    ON owner_stations (user_id) WHERE is_verified = 1;
CREATE INDEX IF NOT EXISTS idx_owner_stations_station
    ON owner_stations (station_id) WHERE is_verified = 1;

-- Отзывы
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
    text TEXT,
    is_visible INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- События
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    payload TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type, created_at DESC);
