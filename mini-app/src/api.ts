// API клиент для Mini App
// Использует backend API для получения станций с реальными статусами.
import type { Station, StationsResponse, StationDetail, FuelType } from "./types";

// В dev: пустая строка → /api/* (vite proxy → localhost:8080)
// В prod: задаётся через VITE_API_URL на Vercel, например https://benzin-api.onrender.com
const API_BASE: string =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "";

// Default timeout для всех запросов (10 сек)
const DEFAULT_TIMEOUT_MS = 10_000;


function buildUrl(path: string, params: Record<string, string | number | undefined>): string {
  const url = new URL(API_BASE + path, window.location.origin);
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") {
      url.searchParams.set(k, String(v));
    }
  });
  return url.toString();
}

async function getJson<T>(url: string, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new Error(`API ${res.status}: ${await res.text()}`);
    }
    return res.json() as Promise<T>;
  } finally {
    clearTimeout(timeout);
  }
}


export async function fetchStations(
  lat: number,
  lon: number,
  radius = 30,
  fuel?: string,
  network?: string,
  max_price?: number,
): Promise<StationsResponse> {
  const data = await getJson<{ stations: Station[]; count: number }>(
    buildUrl("/api/stations", { lat, lon, radius, fuel, network, max_price }),
  );
  return data;
}


export async function searchByCity(
  query: string,
  network?: string,
  max_price?: number,
  fuel?: string,
): Promise<StationsResponse> {
  // /api/stations/by-city — поиск по городу с фильтрами
  // query может быть "Москва" или "Москва Лукойл до 70" — парсим
  const data = await getJson<{ stations: Station[]; count: number; disclaimer?: string }>(
    buildUrl("/api/stations/by-city", {
      city: query,
      network,
      max_price,
      fuel,
    }),
  );
  return data;
}


export async function fetchStationsByCity(
  city: string,
  region?: string,
  fuel?: string,
  network?: string,
  max_price?: number,
): Promise<StationsResponse> {
  const data = await getJson<{ stations: Station[]; count: number }>(
    buildUrl("/api/stations/by-city", { city, region, fuel, network, max_price }),
  );
  return data;
}


export interface ReverseGeocodeResult {
  city: string | null;
  region: string | null;
  country: string | null;
  raw?: Record<string, string>;
}


export async function reverseGeocode(lat: number, lon: number): Promise<ReverseGeocodeResult> {
  return getJson<ReverseGeocodeResult>(buildUrl("/api/reverse-geocode", { lat, lon }));
}


export async function fetchStationDetail(id: number): Promise<StationDetail> {
  return getJson<StationDetail>(buildUrl(`/api/stations/${id}`, {}));
}

export interface PricePoint {
  fuel_type: string;
  price: number | null;
  at: string;
}

export interface PriceSource {
  source: string;
  price: number | null;
  is_best: boolean;
  confidence: number;
  age_hours: number;
  updated_at: string;
}

export interface StationPrices {
  station_id: number;
  fuel_prices: Record<string, {
    best: PriceSource | null;
    all: PriceSource[];
  }>;
  sources_summary: Record<string, number>;
  total_sources: number;
}

export async function fetchPriceHistory(
  id: number,
  fuel: string,
  days = 30,
): Promise<{ station_id: number; fuel: string; history: PricePoint[]; count: number }> {
  return getJson(buildUrl(`/api/stations/${id}/price-history`, { fuel, days }));
}

export async function fetchStationPrices(id: number): Promise<StationPrices> {
  return getJson(buildUrl(`/api/stations/${id}/prices`, {}));
}


export interface PriceUpdate {
  station_id: number;
  fuel_type: FuelType;
  price: number;
  available?: boolean | null;
  queue_size?: number | null;
}

export async function postPriceUpdate(p: PriceUpdate): Promise<{ ok: boolean }> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
  try {
    const res = await fetch(buildUrl("/api/price-update", {}), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  } finally {
    clearTimeout(timeout);
  }
}


/**
 * Отправляет отчёт о наличии.
 *  - В Telegram Mini App: WebApp.sendData (бот примет через web_app_data handler).
 *  - В браузере (PWA / fallback): POST на /api/reports.
 */
export async function postReport(
  stationId: number,
  fuelType: string,
  available: boolean | null,
  extras?: { price?: number; queue_size?: number },
): Promise<{ ok: boolean; source: "telegram" | "api" }> {
  const payload = {
    type: "report",
    station_id: stationId,
    fuel_type: fuelType,
    available: available,
    price: extras?.price,
    queue_size: extras?.queue_size,
    timestamp: Date.now(),
  };

  // 1) Пробуем Telegram Mini App
  try {
    const { default: WebApp } = await import("@twa-dev/sdk");
    if (WebApp?.sendData) {
      WebApp.sendData(JSON.stringify(payload));
      return { ok: true, source: "telegram" };
    }
  } catch {
    // нет TMA SDK
  }

  // 2) Fallback: HTTP POST
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
    try {
      const res = await fetch(buildUrl("/api/reports", {}), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          station_id: stationId,
          fuel_type: fuelType,
          available: available,
          price: extras?.price,
          queue_size: extras?.queue_size,
        }),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return { ok: true, source: "api" };
    } finally {
      clearTimeout(timeout);
    }
  } catch (e) {
    console.error("postReport failed:", e);
    throw e;
  }
}

