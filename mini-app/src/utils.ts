// Хелперы для отображения статуса топлива
import type { Station, FuelStatus } from "./types";

const FUEL_PRIORITY = ["92", "95", "98", "diesel"];

export function getMainStatus(station: Station): {
  icon: "✅" | "❌" | "❓" | "⚠️";
  fuel: string | null;
  label: string;
  cssClass: string;
  markerClass: string;
} {
  if (!station.statuses || station.statuses.length === 0) {
    return {
      icon: "❓",
      fuel: null,
      label: "Нет данных",
      cssClass: "fuel-unk",
      markerClass: "marker-unk",
    };
  }

  for (const fuel of FUEL_PRIORITY) {
    const st = station.statuses.find((s) => s.fuel_type === fuel);
    if (st) return statusToIcon(st);
  }

  return statusToIcon(station.statuses[0]);
}

function statusToIcon(st: FuelStatus) {
  const fuel = st.fuel_type;
  if (st.available === true) {
    return {
      icon: "✅" as const,
      fuel,
      label: st.price ? `АИ-${fuel} · ${st.price}₽` : `АИ-${fuel} есть`,
      cssClass: "fuel-yes",
      markerClass: "marker-yes",
    };
  }
  if (st.available === false) {
    return {
      icon: "❌" as const,
      fuel,
      label: `АИ-${fuel} нет`,
      cssClass: "fuel-no",
      markerClass: "marker-no",
    };
  }
  return {
    icon: "⚠️" as const,
    fuel,
    label: `АИ-${fuel} кончается`,
    cssClass: "fuel-low",
    markerClass: "marker-low",
  };
}

export function formatDistance(km?: number): string {
  if (km == null) return "";
  if (km < 1) return `${Math.round(km * 1000)} м`;
  if (km < 10) return `${km.toFixed(1)} км`;
  return `${Math.round(km)} км`;
}

export function formatAge(iso?: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "";
  const diff = Date.now() - t;
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} ч`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d} дн`;
  return `${Math.floor(d / 7)} нед`;
}

export function getLatestReportAt(station: Station): string | null {
  if (!station.statuses) return null;
  let best: string | null = null;
  for (const s of station.statuses) {
    if (s.last_report_at && (!best || s.last_report_at > best)) {
      best = s.last_report_at;
    } else if (s.created_at && (!best || s.created_at > best)) {
      best = s.created_at;
    }
  }
  return best;
}

export function isStale(station: Station): boolean {
  const t = getLatestReportAt(station);
  if (!t) return true;
  const ageH = (Date.now() - new Date(t).getTime()) / 3_600_000;
  return ageH > 6;
}

export function getBestPrice(station: Station, fuelType?: string): { price: number; fuel: string } | null {
  if (!station.statuses) return null;
  const filtered = fuelType
    ? station.statuses.filter((s) => s.fuel_type === fuelType)
    : station.statuses;
  const priced = filtered.filter((s) => typeof s.price === "number" && s.price > 0);
  if (priced.length === 0) return null;
  priced.sort((a, b) => (a.price || 0) - (b.price || 0));
  return { price: priced[0].price!, fuel: priced[0].fuel_type };
}

export function getQueueSize(station: Station): number {
  if (!station.statuses) return 0;
  let max = 0;
  for (const s of station.statuses) {
    if (typeof s.queue_size === "number" && s.queue_size > max) {
      max = s.queue_size;
    }
  }
  return max;
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string),
  );
}

/** Мини-спарклайн цены: возвращает SVG path (полилиния) для встраивания. */
export function sparklinePath(prices: number[], width = 80, height = 24): string {
  if (prices.length < 2) return "";
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;
  const step = width / (prices.length - 1);
  return prices
    .map((p, i) => {
      const x = i * step;
      const y = height - ((p - min) / range) * height;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

export function trendArrow(prices: number[]): "↗" | "↘" | "→" {
  if (prices.length < 2) return "→";
  const first = prices[0];
  const last = prices[prices.length - 1];
  if (last > first * 1.01) return "↗";
  if (last < first * 0.99) return "↘";
  return "→";
}
