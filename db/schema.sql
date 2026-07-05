-- =====================================================
-- «Бензин рядом» — схема БД
-- Supabase / PostgreSQL
-- =====================================================

-- Расширения
CREATE EXTENSION IF NOT EXISTS postgis;  -- для гео-запросов
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =====================================================
-- АЗС
-- =====================================================
CREATE TABLE IF NOT EXISTS stations (
    id BIGSERIAL PRIMARY KEY,
    osm_id BIGINT UNIQUE,                    -- ID в OpenStreetMap
    name TEXT NOT NULL,
    operator TEXT,                           -- Лукойл, Газпромнефть, ...
    brand TEXT,
    network TEXT,                            -- сеть (для группировки)
    country TEXT DEFAULT 'RU',
    region TEXT,                              -- регион (для фильтров)
    city TEXT,
    address TEXT,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    fuel_types TEXT[],                        -- ['octane_92', 'octane_95', 'diesel']
    has_24_7 BOOLEAN DEFAULT FALSE,
    phone TEXT,
    website TEXT,
    is_verified BOOLEAN DEFAULT FALSE,        -- верифицирована ли АЗС
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stations_geo ON stations (lat, lon);
CREATE INDEX IF NOT EXISTS idx_stations_operator ON stations (operator);
CREATE INDEX IF NOT EXISTS idx_stations_region ON stations (region);
CREATE INDEX IF NOT EXISTS idx_stations_fuel_types ON stations USING GIN (fuel_types);

-- =====================================================
-- Пользователи
-- =====================================================
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    language_code TEXT DEFAULT 'ru',
    reputation INTEGER DEFAULT 50,            -- 0-100, начинается с 50
    total_reports INTEGER DEFAULT 0,          -- сколько отчётов сделал
    confirmed_reports INTEGER DEFAULT 0,      -- сколько подтвердились
    badge TEXT,                                -- 'expert', 'top_reporter', NULL
    region TEXT,                               -- регион пользователя
    city TEXT,
    is_owner BOOLEAN DEFAULT FALSE,            -- владелец АЗС
    is_blocked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users (telegram_id);
CREATE INDEX IF NOT EXISTS idx_users_reputation ON users (reputation DESC);

-- =====================================================
-- Отчёты о наличии топлива
-- =====================================================
CREATE TABLE IF NOT EXISTS reports (
    id BIGSERIAL PRIMARY KEY,
    station_id BIGINT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    fuel_type TEXT NOT NULL,                  -- '92', '95', '98', 'diesel'
    available BOOLEAN NOT NULL,                -- есть / нет
    price NUMERIC(6, 2),                       -- цена за литр (опционально)
    queue_size INTEGER,                        -- 0-5, NULL если не знаем
    has_limit BOOLEAN DEFAULT FALSE,           -- есть ли лимит
    limit_liters INTEGER,                      -- лимит в литрах
    comment TEXT,
    confidence REAL DEFAULT 0.5,                -- 0-1, растёт с подтверждениями
    confirmations INTEGER DEFAULT 0,           -- сколько подтвердили
    disputes INTEGER DEFAULT 0,                 -- сколько опровергли
    source TEXT DEFAULT 'user',                -- 'user', 'owner', 'telegram', 'osm'
    expires_at TIMESTAMPTZ,                    -- когда отчёт считать устаревшим
    next_delivery_at TIMESTAMPTZ,              -- прогноз следующего завоза (если известен)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    -- Fuel quality
    octane_rating REAL,
    cetane_number REAL,
    additives TEXT,
    quality_score REAL,
    fuel_standard TEXT,
    certification TEXT,
    -- Queue data
    queue_wait_minutes INTEGER,
    queue_trend TEXT,
    -- Limits
    limit_per_visit INTEGER,
    limit_daily INTEGER,
    limit_weekly INTEGER,
    -- Reviews
    review_text TEXT,
    rating REAL,
    photos_count INTEGER DEFAULT 0,
    -- Amenities
    has_car_wash BOOLEAN DEFAULT FALSE,
    has_shop BOOLEAN DEFAULT FALSE,
    has_restaurant BOOLEAN DEFAULT FALSE,
    has_atm BOOLEAN DEFAULT FALSE,
    has_parking BOOLEAN DEFAULT FALSE,
    has_ev_charging BOOLEAN DEFAULT FALSE,
    -- Info
    accessibility TEXT,
    opening_hours TEXT,
    phone TEXT,
    website TEXT
);

CREATE INDEX IF NOT EXISTS idx_reports_station ON reports (station_id, fuel_type);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_confidence ON reports (confidence DESC);

-- =====================================================
-- Отзывы о качестве бензина
-- =====================================================
CREATE TABLE IF NOT EXISTS reviews (
    id BIGSERIAL PRIMARY KEY,
    station_id BIGINT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    fuel_type TEXT NOT NULL,                      -- '92', '95', '98', 'diesel'
    rating INTEGER NOT NULL CHECK (rating >= 0 AND rating <= 5),  -- 0-5 звёзд
    comment TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reviews_station ON reviews (station_id, fuel_type);
CREATE INDEX IF NOT EXISTS idx_reviews_created ON reviews (created_at DESC);

-- =====================================================
-- Подписки на уведомления
-- =====================================================
CREATE TABLE IF NOT EXISTS subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_id BIGINT REFERENCES stations(id) ON DELETE CASCADE,
    city TEXT,                                 -- подписка на город
    region TEXT,                               -- подписка на регион
    fuel_type TEXT NOT NULL DEFAULT '92',      -- '92', '95', etc.
    radius_km INTEGER DEFAULT 5,               -- для geo-подписок
    center_lat DOUBLE PRECISION,               -- для geo-подписок
    center_lon DOUBLE PRECISION,
    is_active BOOLEAN DEFAULT TRUE,
    last_notified_at TIMESTAMPTZ,              -- anti-spam
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- либо station_id, либо city, либо region, либо гео
    CONSTRAINT chk_subscription_target CHECK (
        station_id IS NOT NULL OR city IS NOT NULL
        OR region IS NOT NULL OR center_lat IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions (user_id) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_subscriptions_station ON subscriptions (station_id) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_subscriptions_geo ON subscriptions (center_lat, center_lon) WHERE is_active;

-- =====================================================
-- Заявки на регистрацию владельцев АЗС
-- =====================================================
CREATE TABLE IF NOT EXISTS owner_applications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_id BIGINT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    inn TEXT,                                   -- ИНН
    license_photo_url TEXT,                    -- фото лицензии
    sign_photo_url TEXT,                        -- фото вывески
    status TEXT DEFAULT 'pending',              -- 'pending', 'approved', 'rejected'
    moderator_id BIGINT REFERENCES users(id),
    rejection_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    approved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_owner_apps_status ON owner_applications (status);

-- =====================================================
-- Связь владелец ↔ АЗС (после одобрения)
-- =====================================================
CREATE TABLE IF NOT EXISTS owner_stations (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_id BIGINT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    inn TEXT,
    role TEXT DEFAULT 'owner',                  -- 'owner' / 'employee'
    is_verified BOOLEAN DEFAULT FALSE,          -- одобрено модератором
    moderator_id BIGINT REFERENCES users(id),
    rejection_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    verified_at TIMESTAMPTZ,
    UNIQUE(user_id, station_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_stations_user
    ON owner_stations (user_id) WHERE is_verified;
CREATE INDEX IF NOT EXISTS idx_owner_stations_station
    ON owner_stations (station_id) WHERE is_verified;

-- =====================================================
-- Фидбек / отзывы
-- =====================================================
CREATE TABLE IF NOT EXISTS reviews (
    id BIGSERIAL PRIMARY KEY,
    station_id BIGINT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
    text TEXT,
    is_visible BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reviews_station ON reviews (station_id, created_at DESC);

-- =====================================================
-- Аналитика событий (для сбора метрик)
-- =====================================================
CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,                   -- 'bot_start', 'find', 'report', 'push_sent', 'push_clicked'
    payload JSONB,                              -- любые данные
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_user ON events (user_id, created_at DESC);

-- =====================================================
-- Функция для обновления updated_at
-- =====================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_stations_updated_at
    BEFORE UPDATE ON stations
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =====================================================
-- Бейджи пользователей (геймификация)
-- =====================================================
CREATE TABLE IF NOT EXISTS user_badges (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    badge_code TEXT NOT NULL,
    awarded_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, badge_code)
);

CREATE INDEX IF NOT EXISTS idx_user_badges_user ON user_badges (user_id);

-- =====================================================
-- Premium-подписки (Telegram Stars)
-- =====================================================
CREATE TABLE IF NOT EXISTS premium_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    telegram_payment_charge_id TEXT,
    stars_amount INTEGER,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_premium_user ON premium_subscriptions (user_id) WHERE is_active;

-- =====================================================
-- View: текущий статус АЗС (последний отчёт по каждому типу топлива)
-- =====================================================
DROP VIEW IF EXISTS station_current_status;
CREATE OR REPLACE VIEW station_current_status AS
SELECT DISTINCT ON (s.id, r.fuel_type)
    s.id AS station_id,
    s.name,
    s.lat,
    s.lon,
    s.operator,
    s.city,
    s.region,
    r.fuel_type,
    r.available,
    r.price,
    r.queue_size,
    r.has_limit,
    r.limit_liters,
    r.confidence,
    r.source,
    r.created_at AS last_report_at,
    EXTRACT(EPOCH FROM (NOW() - r.created_at)) AS seconds_since_report,
    CASE
        WHEN r.created_at > NOW() - INTERVAL '30 minutes' THEN 'fresh'
        WHEN r.created_at > NOW() - INTERVAL '2 hours' THEN 'recent'
        WHEN r.created_at > NOW() - INTERVAL '6 hours' THEN 'stale'
        ELSE 'expired'
    END AS freshness
FROM stations s
LEFT JOIN LATERAL (
    SELECT *
    FROM reports
    WHERE station_id = s.id
    AND (
        -- Парсерские отчёты: только за 2 часа (старые удаляются парсерами)
        (source != 'user' AND created_at > NOW() - INTERVAL '2 hours')
        OR
        -- Пользовательские отчёты: живут 7 дней или пока не противоречит парсер
        (source = 'user' AND created_at > NOW() - INTERVAL '7 days')
    )
    ORDER BY 
        CASE WHEN source = 'user' THEN 0 ELSE 1 END,  -- пользовательские приоритетнее
        confidence DESC, 
        created_at DESC
    LIMIT 1
) r ON true
WHERE s.is_active = TRUE;

COMMENT ON TABLE stations IS 'Заправочные станции';
COMMENT ON TABLE users IS 'Пользователи бота';
COMMENT ON TABLE reports IS 'Отчёты о наличии топлива';
COMMENT ON TABLE subscriptions IS 'Подписки на уведомления';
COMMENT ON TABLE reviews IS 'Отзывы об АЗС';
COMMENT ON TABLE events IS 'Аналитика событий';
COMMENT ON VIEW station_current_status IS 'Текущий статус АЗС по типам топлива';
