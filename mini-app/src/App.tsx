import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import WebApp from "@twa-dev/sdk";
import {
  fetchStations,
  searchByCity,
  fetchStationsByCity,
  reverseGeocode,
  postReport,
  fetchPriceHistory,
  fetchStationPrices,
  type PricePoint,
  type StationPrices,
} from "./api";
import type { Station, FuelType } from "./types";
import {
  getMainStatus,
  formatDistance,
  formatAge,
  getLatestReportAt,
  isStale,
  getBestPrice,
  getQueueSize,
  escapeHtml,
  sparklinePath,
  trendArrow,
} from "./utils";
import { getUserLocation, getGPSLocation } from "./geolocation";

const FUEL_FILTERS: { key: string; label: string }[] = [
  { key: "", label: "⛽ Всё" },
  { key: "92", label: "АИ-92" },
  { key: "95", label: "АИ-95" },
  { key: "98", label: "АИ-98" },
  { key: "diesel", label: "Дизель" },
  { key: "lpg", label: "Газ" },
];

const NETWORK_FILTERS = [
  "Лукойл", "Газпромнефть", "Роснефть", "Татнефть", "Башнефть",
  "Shell", "Teboil", "СНГ", "Нефтьмагистраль", "Трасса",
];

const DEFAULT_CENTER: [number, number] = [55.7558, 37.6173];
const DEFAULT_MARKER_LIMIT = 300;

function useSeo() {
  useEffect(() => {
    const path = window.location.pathname.replace(/^\//, "").toLowerCase();
    const titles: Record<string, { title: string; description: string }> = {
      "": {
        title: "Бензин рядом — карта АЗС в реальном времени",
        description:
          "Где сейчас есть бензин в России. Карта АЗС, отчёты водителей, push-уведомления о завозе.",
      },
      ivanovo: {
        title: "Бензин в Иваново — где есть сейчас",
        description:
          "Карта АЗС Иванова: наличие бензина, очереди, цены. Актуальные отчёты водителей.",
      },
      "msk-95": {
        title: "АИ-95 в Москве — где есть сейчас",
        description:
          "Карта АЗС Москвы с наличием АИ-95. Отчёты водителей в реальном времени.",
      },
      crimea: {
        title: "Бензин в Крыму — карта АЗС",
        description:
          "Где есть бензин в Крыму: Симферополь, Севастополь, Ялта. Актуальные отчёты.",
      },
    };
    const seo = titles[path] || titles[""];
    document.title = seo.title;
    const setMeta = (name: string, content: string) => {
      let el = document.querySelector(`meta[name="${name}"]`) as HTMLMetaElement | null;
      if (!el) {
        el = document.createElement("meta");
        el.setAttribute("name", name);
        document.head.appendChild(el);
      }
      el.setAttribute("content", content);
    };
    setMeta("description", seo.description);
    setMeta("og:title", seo.title);
    setMeta("og:description", seo.description);
  }, []);
}

const MAP_STYLE: any = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: [
        "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "https://b.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "https://c.tile.openstreetmap.org/{z}/{x}/{y}.png",
      ],
      tileSize: 256,
      attribution: "© OpenStreetMap",
    },
  },
  layers: [
    { id: "background", type: "background", paint: { "background-color": "#0a0a0f" } },
    {
      id: "osm-tiles",
      type: "raster",
      source: "osm",
      paint: {
        "raster-saturation": -0.7,
        "raster-brightness-min": 0.1,
        "raster-brightness-max": 0.55,
        "raster-contrast": 0.15,
      },
    },
  ],
};

export default function App() {
  const [center, setCenter] = useState<[number, number]>(DEFAULT_CENTER);
  const [stations, setStations] = useState<Station[]>([]);
  const [loading, setLoading] = useState(false);
  const [dataLoading, setDataLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [fuelFilter, setFuelFilter] = useState("");
  const [networkFilter, setNetworkFilter] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [selected, setSelected] = useState<Station | null>(null);
  const [showList, setShowList] = useState(true);
  const [tgUser, setTgUser] = useState<any>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [cityHint, setCityHint] = useState<string | null>(null);

  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapInstanceRef = useRef<maplibregl.Map | null>(null);
  const userMarkerRef = useRef<maplibregl.Marker | null>(null);
  const stationMarkersRef = useRef<maplibregl.Marker[]>([]);
  const mapInitializedRef = useRef(false);

  const [toast, setToast] = useState<{ text: string; kind: "ok" | "err" | "info" } | null>(null);
  const showToast = (text: string, kind: "ok" | "err" | "info" = "info") => {
    setToast({ text, kind });
    setTimeout(() => setToast(null), 3000);
  };

  // === Онбординг (показывается один раз) ===
  const [showOnboarding, setShowOnboarding] = useState(false);
  useEffect(() => {
    try {
      const done = localStorage.getItem("benzin_onboarded");
      if (!done) setShowOnboarding(true);
    } catch {}
  }, []);
  const completeOnboarding = () => {
    try {
      localStorage.setItem("benzin_onboarded", "1");
    } catch {}
    setShowOnboarding(false);
  };

  useSeo();

  useEffect(() => {
    try {
      WebApp.ready();
      WebApp.expand();
      const u = WebApp.initDataUnsafe?.user;
      if (u) setTgUser(u);
    } catch {}
  }, []);

  // === Premium статус ===
  const [isPremium, setIsPremium] = useState(false);

  // === Сортировка: verified > свежие с ценами > свежие > остальные ===
  const sortedStations = useMemo(() => {
    return [...stations].sort((a, b) => {
      const av = a.is_verified ? 1 : 0;
      const bv = b.is_verified ? 1 : 0;
      if (av !== bv) return bv - av;
      const ap = getBestPrice(a) ? 1 : 0;
      const bp = getBestPrice(b) ? 1 : 0;
      if (ap !== bp) return bp - ap;
      const ad = (a.distance_km ?? 9999) - (b.distance_km ?? 9999);
      return ad;
    });
  }, [stations]);

  const loadStations = useCallback(
    async (lat: number, lon: number, fuel: string, silent = false) => {
      if (!silent) setLoading(true);
      setErrorMsg(null);
    try {
      const maxPriceNum = maxPrice ? parseFloat(maxPrice) : undefined;
      const data = await fetchStations(
        lat, lon, 100,
        fuel || undefined,
        networkFilter || undefined,
        maxPriceNum,
      );
      setStations(data.stations);
      if (data.is_premium !== undefined) setIsPremium(data.is_premium);
      setLastUpdate(new Date());
      if (mapInstanceRef.current) {
        updateMarkersOnMap(mapInstanceRef.current, data.stations);
        fitMapToStations(mapInstanceRef.current, data.stations, [lon, lat]);
      }
    } catch (e: any) {
        console.error("Load error:", e);
        setErrorMsg("Ошибка загрузки данных");
        if (!silent) showToast("Ошибка загрузки данных", "err");
      } finally {
        setLoading(false);
        setDataLoading(false);
        setRefreshing(false);
      }
    },
    [networkFilter, maxPrice],
  );

  useEffect(() => {
    (async () => {
      try {
        const loc = await getUserLocation();
        if (loc.source !== "default") {
          setCenter([loc.lat, loc.lon]);
          // Автоопределение города
          try {
            const geo = await reverseGeocode(loc.lat, loc.lon);
            if (geo.city) {
              setCityHint(geo.city);
            }
          } catch {
            // ignore
          }
        }
        await loadStations(
          loc.source === "default" ? DEFAULT_CENTER[0] : loc.lat,
          loc.source === "default" ? DEFAULT_CENTER[1] : loc.lon,
          fuelFilter,
        );
      } catch {
        loadStations(center[0], center[1], fuelFilter);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadStations(center[0], center[1], fuelFilter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fuelFilter, networkFilter, maxPrice]);

  // === Premium badge (тихая проверка при запуске) ===
  useEffect(() => {
    if (tgUser?.id) {
      fetch(`https://benzin-ryadom.onrender.com/api/premium-status?tg=${tgUser.id}`)
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d?.is_premium) setIsPremium(true); })
        .catch(() => {});
    }
  }, [tgUser?.id]);

  function createMarkerElement(s: Station): HTMLDivElement {
    const status = getMainStatus(s);
    const el = document.createElement("div");
    el.className = `premium-marker ${status.markerClass}`;
    if (s.is_verified) el.style.color = "#22c55e";
    el.innerHTML = `
      <div class="marker-glow"></div>
      <div class="marker-core">
        <svg viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2C7.58 2 4 5.58 4 10c0 5.25 7 12 8 12s8-6.75 8-12c0-4.42-3.58-8-8-8zm0 11a3 3 0 110-6 3 3 0 010 6z"/>
        </svg>
      </div>
    `;
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      setSelected(s);
      try {
        WebApp.HapticFeedback?.impactOccurred?.("light");
      } catch {}
    });
    return el;
  }

  function createUserMarkerElement(): HTMLDivElement {
    const el = document.createElement("div");
    el.className = "user-location-marker";
    el.innerHTML = `<div class="user-pulse"></div><div class="user-dot"></div>`;
    return el;
  }

  function createPopupHTML(s: Station): string {
    const status = getMainStatus(s);
    const address = s.address || s.city || "";
    const verifiedBadge = s.is_verified
      ? `<span class="popup-verified">✓ Verified</span>`
      : "";
    const bestPrice = getBestPrice(s);
    return `
      <div class="popup-inner">
        <div class="popup-name">${escapeHtml(s.name || "АЗС")} ${verifiedBadge}</div>
        ${s.operator && s.operator !== s.name ? `<div class="popup-meta">${escapeHtml(s.operator)}</div>` : ""}
        ${address ? `<div class="popup-addr">📍 ${escapeHtml(address)}</div>` : ""}
        <div class="popup-status ${status.cssClass}">${status.icon} ${escapeHtml(status.label)}</div>
        ${bestPrice ? `<div class="popup-price">от ${bestPrice.price}₽ · АИ-${bestPrice.fuel}</div>` : ""}
      </div>
    `;
  }

  function updateMarkersOnMap(map: maplibregl.Map, data: Station[]) {
    stationMarkersRef.current.forEach((m) => m.remove());
    stationMarkersRef.current = [];
    const toShow = data.slice(0, DEFAULT_MARKER_LIMIT);
    toShow.forEach((s) => {
      const el = createMarkerElement(s);
      const popup = new maplibregl.Popup({
        offset: 28,
        closeButton: false,
        className: "premium-popup",
      }).setHTML(createPopupHTML(s));
      const marker = new maplibregl.Marker({ element: el })
        .setLngLat([s.lon, s.lat])
        .setPopup(popup)
        .addTo(map);
      stationMarkersRef.current.push(marker);
    });
  }

  function fitMapToStations(map: maplibregl.Map, data: Station[], focus?: [number, number]) {
    if (data.length === 0) return;
    const bounds = new maplibregl.LngLatBounds();
    data.slice(0, 200).forEach((s) => bounds.extend([s.lon, s.lat]));
    if (focus) bounds.extend(focus);
    if (!bounds.isEmpty()) {
      map.fitBounds(bounds, { padding: 60, maxZoom: 14, duration: 800 });
    }
  }

  useEffect(() => {
    if (showList || mapInitializedRef.current || !mapContainerRef.current) return;
    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: MAP_STYLE,
      center,
      zoom: 11,
      minZoom: 3,
      maxZoom: 18,
      pitchWithRotate: false,
      dragRotate: false,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
    map.on("load", () => {
      mapInitializedRef.current = true;
      mapInstanceRef.current = map;
      const userEl = createUserMarkerElement();
      const userMarker = new maplibregl.Marker({ element: userEl }).setLngLat(center).addTo(map);
      userMarkerRef.current = userMarker;
      if (stations.length > 0) {
        updateMarkersOnMap(map, stations);
        fitMapToStations(map, stations, center);
      }
    });
    return () => {
      stationMarkersRef.current.forEach((m) => m.remove());
      stationMarkersRef.current = [];
      if (userMarkerRef.current) {
        userMarkerRef.current.remove();
        userMarkerRef.current = null;
      }
      map.remove();
      mapInstanceRef.current = null;
      mapInitializedRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showList]);

  useEffect(() => {
    if (mapInitializedRef.current && mapInstanceRef.current) {
      updateMarkersOnMap(mapInstanceRef.current, stations);
    }
  }, [stations, fuelFilter]);

  useEffect(() => {
    if (userMarkerRef.current) userMarkerRef.current.setLngLat(center);
    if (mapInstanceRef.current && mapInitializedRef.current) {
      mapInstanceRef.current.flyTo({ center, duration: 600 });
    }
  }, [center]);

  const requestLocation = async () => {
    setErrorMsg(null);
    setLoading(true);
    try {
      const loc = await getGPSLocation();
      setCenter([loc.lat, loc.lon]);
      loadStations(loc.lat, loc.lon, fuelFilter);
      try {
        WebApp.HapticFeedback?.impactOccurred?.("medium");
      } catch {}
    } catch (e: any) {
      try {
        const loc = await getUserLocation();
        if (loc.source === "ip") {
          setCenter([loc.lat, loc.lon]);
          loadStations(loc.lat, loc.lon, fuelFilter);
          setErrorMsg("Точная геолокация недоступна, используем приближённую по IP");
        } else {
          setErrorMsg(e.message || "Не удалось определить местоположение");
        }
      } catch {
        setErrorMsg("Введи город в поиск");
      }
    } finally {
      setLoading(false);
    }
  };

  const onRefresh = async () => {
    setRefreshing(true);
    await loadStations(center[0], center[1], fuelFilter, true);
    try {
      WebApp.HapticFeedback?.impactOccurred?.("light");
    } catch {}
  };

  const handleSearch = async () => {
    if (!searchQuery.trim() || searchQuery.length < 2) return;
    setSearching(true);
    setErrorMsg(null);
    try {
      const maxPriceNum = maxPrice ? parseFloat(maxPrice) : undefined;
      const data = await searchByCity(
        searchQuery.trim(),
        networkFilter || undefined,
        maxPriceNum,
        fuelFilter || undefined,
      );
      setStations(data.stations);
      if (data.stations.length > 0) {
        const first = data.stations[0];
        setCenter([first.lat, first.lon]);
      } else {
        setErrorMsg(`По запросу «${searchQuery}» ничего не найдено`);
      }
    } catch {
      setErrorMsg("Ошибка поиска");
    } finally {
      setSearching(false);
    }
  };

  // === Подсчёт статистики для header ===
  const stats = useMemo(() => {
    let withFuel = 0;
    let verified = 0;
    let cheap = 0;
    for (const s of stations) {
      if (s.has_data) withFuel++;
      if (s.is_verified) verified++;
      const best = getBestPrice(s);
      if (best && best.price < 55) cheap++;
    }
    return { withFuel, verified, cheap, total: stations.length };
  }, [stations]);

  return (
    <div className="h-full flex flex-col text-white overflow-hidden">
      {/* Hero header */}
      <div className="hero-header flex-shrink-0 px-4 pt-3 pb-2.5 z-20">
        <div className="flex items-center justify-between mb-2.5">
          <div className="flex items-center gap-2">
            <div
              className="w-9 h-9 rounded-xl flex items-center justify-center text-xl"
              style={{
                background: "linear-gradient(135deg, #ff1e3c 0%, #c8102e 100%)",
                boxShadow: "0 4px 12px rgba(255, 30, 60, 0.4)",
              }}
            >
              ⛽
            </div>
            <div>
              <div className="text-base font-bold tracking-tight leading-tight">
                Бензин рядом
                {isPremium && (
                  <span className="ml-2 verified-badge" title="Premium активен">💎 Premium</span>
                )}
              </div>
              <div className="text-[10px] text-white/40 leading-tight flex items-center gap-1">
                <span className="live-dot" />
                <span>live · {cityHint || "определяю…"}</span>
                {/* FIXME: city из ipapi добавить отдельно */}
              </div>
            </div>
          </div>
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="btn-glass text-xs flex items-center gap-1.5"
            title="Обновить"
          >
            <span style={{ display: "inline-block", animation: refreshing ? "spin 0.8s linear infinite" : "none" }}>
              ↻
            </span>
            <span>{refreshing ? "…" : lastUpdate ? formatAge(lastUpdate.toISOString()) : "обновить"}</span>
          </button>
        </div>

        {/* Search */}
        <div className="flex gap-2 mb-2.5">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="Город, сеть или название АЗС…"
            className="flex-1 bg-white/5 border border-white/10 rounded-xl px-3.5 py-2.5 text-sm placeholder-white/30 focus:outline-none focus:border-accent/50"
            style={{ color: "#fff" }}
          />
          <button
            onClick={handleSearch}
            disabled={searching || searchQuery.length < 2}
            className="btn-primary text-sm disabled:opacity-50"
          >
            {searching ? "…" : "Найти"}
          </button>
        </div>

        {/* Filter chips */}
        <div className="flex gap-1.5 overflow-x-auto pb-1 -mx-1 px-1 scrollbar-hide">
          {FUEL_FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFuelFilter(f.key)}
              className={`chip whitespace-nowrap transition-all ${
                fuelFilter === f.key ? "chip-active" : "chip-inactive"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {/* Network + max_price filters */}
        <div className="flex gap-1.5 mt-2">
          <select
            value={networkFilter}
            onChange={(e) => setNetworkFilter(e.target.value)}
            className="flex-1 bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-xs"
            style={{ color: "#fff" }}
          >
            <option value="">⛽ Все сети</option>
            {NETWORK_FILTERS.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          <select
            value={maxPrice}
            onChange={(e) => setMaxPrice(e.target.value)}
            className="flex-1 bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-xs"
            style={{ color: "#fff" }}
          >
            <option value="">💰 Любая цена</option>
            <option value="50">до 50₽</option>
            <option value="60">до 60₽</option>
            <option value="70">до 70₽</option>
            <option value="80">до 80₽</option>
            <option value="100">до 100₽</option>
          </select>
        </div>

        {/* Disclaimer (компактно) */}
        <details className="mt-2 text-[10px] text-white/40">
          <summary className="cursor-pointer hover:text-white/60">
            ⚠️ Дисклеймер
          </summary>
          <div className="mt-1 px-2 py-1.5 bg-white/5 rounded-lg leading-relaxed">
            Цены и наличие обновляются пользователями и парсерами
            (fuelprice.ru, 2ГИС, Telegram-каналы). Возможны задержки.
            Перед поездкой перезвоните на АЗС.
            Бот не несёт ответственности за достоверность данных.
          </div>
        </details>

        {/* Stats row — только когда есть данные */}
        {!dataLoading && stations.length > 0 && (
          <div className="grid grid-cols-3 gap-1.5 mt-2.5">
            <div className="stat-card">
              <div className="value text-success">{stats.withFuel}</div>
              <div className="label">с топливом</div>
            </div>
            <div className="stat-card">
              <div className="value">{stats.verified}</div>
              <div className="label">verified</div>
            </div>
            <div className="stat-card">
              <div className="value text-accent">₽{stats.cheap > 0 ? "<55" : "—"}</div>
              <div className="label">дешёвых</div>
            </div>
          </div>
        )}
      </div>

      {/* Контент */}
      <div className="flex-1 min-h-0 relative">
        {dataLoading ? (
          <div className="h-full flex items-center justify-center text-white/60">
            <div className="text-center">
              <div className="text-5xl mb-3" style={{ animation: "spin 1.5s linear infinite", display: "inline-block" }}>⏳</div>
              <div className="text-sm">Загружаю базу АЗС…</div>
            </div>
          </div>
        ) : showList ? (
          <StationList
            stations={sortedStations}
            loading={loading}
            errorMsg={errorMsg}
            onSelect={setSelected}
            onRetry={requestLocation}
          />
        ) : (
          <div ref={mapContainerRef} className="absolute inset-0" />
        )}

        {/* FAB «Где я» */}
        {!dataLoading && (
          <button
            onClick={requestLocation}
            className="fab-secondary"
            style={{ bottom: 84, right: 20, width: 48, height: 48 }}
            title="Где я"
          >
            📍
          </button>
        )}

        {/* FAB «Карта / Список» */}
        {!dataLoading && (
          <button
            onClick={() => setShowList((v) => !v)}
            className="fab"
            title={showList ? "Показать карту" : "Показать список"}
          >
            {showList ? "🗺" : "📋"}
          </button>
        )}
      </div>

      {selected && (
        <StationDetail
          station={selected}
          onClose={() => setSelected(null)}
          onReport={async (fuel, available, extras) => {
            try {
              await postReport(selected.id, fuel, available, extras);
              try {
                WebApp.HapticFeedback?.notificationOccurred?.("success");
              } catch {}
              showToast("Спасибо! Отчёт записан ✅", "ok");
              // optimistic update
              setStations((prev) =>
                prev.map((s) =>
                  s.id === selected.id
                    ? {
                        ...s,
                        statuses: [
                          {
                            fuel_type: fuel,
                            available: available === undefined ? null : available,
                            price: extras?.price ?? null,
                            queue_size: extras?.queue_size ?? null,
                            last_report_at: new Date().toISOString(),
                          } as any,
                          ...s.statuses.filter((st) => st.fuel_type !== fuel),
                        ],
                        has_data: true,
                      }
                    : s,
                ),
              );
              setSelected(null);
            } catch {
              showToast("Не удалось отправить отчёт. Попробуй ещё раз.", "err");
            }
          }}
        />
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed top-4 left-4 right-4 z-[3000] flex justify-center pointer-events-none">
          <div
            className={`toast px-4 py-3 rounded-xl text-sm font-medium shadow-2xl max-w-sm ${
              toast.kind === "ok"
                ? "bg-success text-white"
                : toast.kind === "err"
                  ? "bg-danger text-white"
                  : "bg-card text-white border border-white/10"
            }`}
          >
            {toast.text}
          </div>
        </div>
      )}

      {/* Онбординг — 3 экрана */}
      {showOnboarding && <Onboarding onDone={completeOnboarding} />}
    </div>
  );
}

// === Онбординг — 3 слайда ===
function Onboarding({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(0);
  const slides = [
    {
      emoji: "🗺",
      title: "Найди ближайшую АЗС",
      text: "Твоя геолокация — и список АЗС вокруг. Фильтры по АИ-92, 95, 98, дизелю и газу. Точные адреса и цены.",
      bg: "linear-gradient(135deg, #1e3a8a 0%, #312e81 100%)",
    },
    {
      emoji: "📝",
      title: "Помогай другим",
      text: "Отмечай: ✅ есть, ⚠️ кончается, ❌ нет. Указывай цену и длину очереди. Получай бейджи за активность!",
      bg: "linear-gradient(135deg, #ff1e3c 0%, #c8102e 100%)",
    },
    {
      emoji: "🔔",
      title: "Push о завозе",
      text: "Подпишись на АЗС или район — получи уведомление, когда появится топливо или упадёт цена.",
      bg: "linear-gradient(135deg, #16a34a 0%, #15803d 100%)",
    },
  ];
  const s = slides[step];
  const isLast = step === slides.length - 1;
  return (
    <div
      className="fixed inset-0 z-[5000] flex flex-col items-center justify-center p-6"
      style={{ background: s.bg, animation: "fadeIn 0.3s" }}
    >
      {/* Progress dots */}
      <div className="absolute top-6 left-0 right-0 flex justify-center gap-2">
        {slides.map((_, i) => (
          <div
            key={i}
            className="h-1.5 rounded-full transition-all"
            style={{
              width: i === step ? 32 : 8,
              background: i === step ? "#fff" : "rgba(255,255,255,0.4)",
            }}
          />
        ))}
      </div>

      <div
        className="text-8xl mb-6"
        style={{ animation: "scaleIn 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)" }}
      >
        {s.emoji}
      </div>
      <div
        className="text-2xl font-bold text-white text-center mb-3 tracking-tight"
        key={step}
        style={{ animation: "slideIn 0.3s ease-out" }}
      >
        {s.title}
      </div>
      <div
        className="text-base text-white/80 text-center max-w-xs mb-12 leading-relaxed"
        key={`t${step}`}
        style={{ animation: "slideIn 0.3s ease-out 0.1s both" }}
      >
        {s.text}
      </div>

      <div className="flex gap-3 w-full max-w-sm">
        {!isLast && (
          <button onClick={onDone} className="btn-glass text-white flex-shrink-0" style={{ flex: 0 }}>
            Пропустить
          </button>
        )}
        <button
          onClick={() => (isLast ? onDone() : setStep(step + 1))}
          className="flex-1 btn-primary"
        >
          {isLast ? "🚀 Начать" : "Дальше →"}
        </button>
      </div>
    </div>
  );
}

// === StationList ===
function StationList({
  stations,
  loading,
  errorMsg,
  onSelect,
  onRetry,
}: {
  stations: Station[];
  loading: boolean;
  errorMsg: string | null;
  onSelect: (s: Station) => void;
  onRetry: () => void;
}) {
  if (loading && stations.length === 0) return <SkeletonList />;
  if (stations.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-white/70 px-6 text-center">
        <div className="max-w-xs">
          <div className="text-5xl mb-3 opacity-50">🔍</div>
          <div className="text-base font-medium mb-2">АЗС не найдены</div>
          <div className="text-sm text-white/40 mb-4">
            {errorMsg || "В этом районе пока нет АЗС в базе"}
          </div>
          <button onClick={onRetry} className="btn-primary text-sm">
            📍 Определить местоположение
          </button>
        </div>
      </div>
    );
  }
  return (
    <div
      className="h-full overflow-y-auto p-3 space-y-2"
      style={{ WebkitOverflowScrolling: "touch" }}
    >
      {stations.map((s, i) => (
        <StationCard
          key={s.id}
          station={s}
          index={i}
          onClick={() => onSelect(s)}
        />
      ))}
    </div>
  );
}

// === StationCard — premium card с ценами, verified, возрастом ===
function StationCard({
  station: s,
  index,
  onClick,
}: {
  station: Station;
  index: number;
  onClick: () => void;
}) {
  const st = getMainStatus(s);
  const lastReport = getLatestReportAt(s);
  const stale = isStale(s);
  const bestPrice = getBestPrice(s);
  const queue = getQueueSize(s);
  const addressLine =
    s.address ||
    (s.city ? `г. ${s.city}` : "") ||
    (s.operator ? `Сеть: ${s.operator}` : "");

  return (
    <div
      className={`station-row ${s.is_verified ? "verified" : ""}`}
      onClick={onClick}
      style={{ animation: `slideIn 0.3s ease-out ${Math.min(index * 0.015, 0.4)}s both` }}
    >
      <div className={`fuel-badge ${st.cssClass}`}>{st.icon}</div>
      <div className="flex-1 min-w-0">
        <div className="font-semibold truncate text-[15px] flex items-center gap-1.5">
          <span className="truncate">{s.name || "АЗС"}</span>
          {s.is_verified && (
            <span className="verified-badge flex-shrink-0">✓ Владелец</span>
          )}
        </div>
        {s.operator && s.operator !== s.name && (
          <div className="text-xs text-white/50 truncate">{s.operator}</div>
        )}
        {addressLine && (
          <div className="text-xs text-white/60 truncate mt-0.5 font-mono">
            📍 {addressLine}
          </div>
        )}
        <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
          <span
            className={`text-xs font-medium ${
              st.cssClass === "fuel-yes"
                ? "text-success"
                : st.cssClass === "fuel-no"
                  ? "text-danger"
                  : st.cssClass === "fuel-low"
                    ? "text-warn"
                    : "text-white/40"
            }`}
          >
            {st.icon} {st.label}
          </span>
          {bestPrice && (
            <span className="price-chip">от {bestPrice.price}₽</span>
          )}
          {queue > 0 && (
            <span className="queue-warning">🕐 ~{queue}</span>
          )}
          {s.distance_km != null && (
            <span className="text-xs text-white/30">· {formatDistance(s.distance_km)}</span>
          )}
        </div>
        {lastReport && (
          <div className={`freshness mt-1 ${stale ? "stale" : "fresh"}`}>
            <span className="dot" />
            <span>обновлено {formatAge(lastReport)} назад</span>
          </div>
        )}
      </div>
      <svg
        className="w-4 h-4 text-white/30 flex-shrink-0"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth="2"
      >
        <path d="M9 5l7 7-7 7" />
      </svg>
    </div>
  );
}

function SkeletonList() {
  return (
    <div className="h-full overflow-y-auto p-3 space-y-2">
      {[1, 2, 3, 4, 5, 6].map((i) => (
        <div key={i} className="station-row">
          <div className="w-10 h-10 rounded-full skeleton" />
          <div className="flex-1 space-y-2">
            <div className="h-4 w-3/4 rounded skeleton" />
            <div className="h-3 w-1/2 rounded skeleton" />
            <div className="h-3 w-2/3 rounded skeleton" />
          </div>
        </div>
      ))}
    </div>
  );
}

// === StationDetail — premium sheet ===
function StationDetail({
  station,
  onClose,
  onReport,
}: {
  station: Station;
  onClose: () => void;
  onReport: (fuel: string, available: boolean | null, extras?: { price?: number; queue_size?: number }) => void;
}) {
  const st = getMainStatus(station);
  const address = station.address || station.city || "—";
  const [history, setHistory] = useState<PricePoint[] | null>(null);
  const [prices, setPrices] = useState<StationPrices | null>(null);
  const [reportFuel, setReportFuel] = useState<string>("95");
  const [reportPrice, setReportPrice] = useState<string>("");
  const [reportQueue, setReportQueue] = useState<string>("");
  const [showPriceInput, setShowPriceInput] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [histData, pricesData] = await Promise.all([
          fetchPriceHistory(station.id, "95", 30).catch(() => ({ history: [] })),
          fetchStationPrices(station.id).catch(() => null),
        ]);
        if (!cancelled) {
          setHistory(histData.history || []);
          setPrices(pricesData);
        }
      } catch {
        if (!cancelled) {
          setHistory([]);
          setPrices(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [station.id]);

  const openRoute = (provider: "yandex" | "2gis") => {
    const { lat, lon } = station;
    const url =
      provider === "yandex"
        ? `https://yandex.ru/maps/?rtext=~${lat},${lon}&rtt=auto`
        : `https://2gis.ru/directions?point=current&point=${lat},${lon}`;
    try {
      WebApp.openLink(url);
    } catch {
      window.open(url, "_blank", "noopener");
    }
  };

  const submitReport = (available: boolean | null) => {
    const extras: { price?: number; queue_size?: number } = {};
    if (reportPrice && !isNaN(Number(reportPrice))) extras.price = Number(reportPrice);
    if (reportQueue && !isNaN(Number(reportQueue))) extras.queue_size = Number(reportQueue);
    onReport(reportFuel, available, Object.keys(extras).length > 0 ? extras : undefined);
  };

  return (
    <div
      className="fixed inset-0 z-[2000] bg-black/70 flex items-end"
      onClick={onClose}
      style={{ animation: "fadeIn 0.2s" }}
    >
      <div
        className="w-full bg-bg rounded-t-3xl p-5 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
        style={{ WebkitOverflowScrolling: "touch", animation: "slideUp 0.3s ease-out" }}
      >
        <div className="w-12 h-1.5 bg-white/20 rounded-full mx-auto mb-4" />

        {/* Hero header */}
        <div className="flex items-start gap-3 mb-4">
          <div
            className={`fuel-badge ${st.cssClass}`}
            style={{ width: 52, height: 52, fontSize: 24 }}
          >
            {st.icon}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-2xl font-bold tracking-tight flex items-center gap-2 flex-wrap">
              <span>{station.name}</span>
              {station.is_verified && (
                <span className="verified-badge" title="Подтверждено владельцем">
                  ✓ Владелец
                </span>
              )}
            </div>
            {station.operator && station.operator !== station.name && (
              <div className="text-sm text-white/50 mt-0.5">{station.operator}</div>
            )}
            <div className="text-sm text-white/70 mt-1 flex items-start gap-1">
              <span>📍</span>
              <span className="flex-1">
                {address !== "—" ? address : `${station.lat.toFixed(4)}, ${station.lon.toFixed(4)}`}
              </span>
            </div>
            {station.distance_km != null && (
              <div className="text-xs text-white/40 mt-0.5">
                {formatDistance(station.distance_km)} от вас
              </div>
            )}
          </div>
          <button onClick={onClose} className="text-white/40 text-2xl px-2 leading-none">
            ×
          </button>
        </div>

        {/* Маршрут */}
        <div className="grid grid-cols-2 gap-2 mb-4">
          <button onClick={() => openRoute("yandex")} className="btn bg-card hover:bg-white/10 text-sm">
            🗺 Яндекс.Карты
          </button>
          <button onClick={() => openRoute("2gis")} className="btn bg-card hover:bg-white/10 text-sm">
            🗺 2ГИС
          </button>
        </div>

        {/* Наличие по типам топлива */}
        <div className="text-xs uppercase tracking-wider text-white/40 mb-2 font-semibold">
          Топливо
        </div>
        {!station.has_data ? (
          <div className="text-white/60 text-sm mb-4 p-4 bg-card rounded-2xl text-center border border-white/5">
            <div className="text-2xl mb-1">❓</div>
            Нет данных. Будь первым — сообщи!
          </div>
        ) : (
          <div className="space-y-2 mb-4">
            {station.statuses.map((s, i) => {
              const ageColor =
                s.last_report_at && (Date.now() - new Date(s.last_report_at).getTime()) < 3_600_000
                  ? "text-success"
                  : "text-white/30";
              return (
                <div
                  key={i}
                  className="flex items-center justify-between p-3 bg-card rounded-xl border border-white/5"
                >
                  <div className="flex items-center gap-3">
                    <span
                      style={{ fontSize: 20 }}
                      className={
                        s.available === true
                          ? "text-success"
                          : s.available === false
                            ? "text-danger"
                            : "text-warn"
                      }
                    >
                      {s.available === true ? "✅" : s.available === false ? "❌" : "⚠️"}
                    </span>
                    <span className="font-semibold text-base">АИ-{s.fuel_type}</span>
                    {s.queue_size ? (
                      <span className="queue-warning">🕐 ~{s.queue_size}</span>
                    ) : null}
                  </div>
                  <div className="text-sm text-right">
                    {s.price ? <div className="font-bold text-base">{s.price}₽</div> : null}
                    {s.last_report_at && (
                      <div className={`text-[10px] ${ageColor}`}>
                        {formatAge(s.last_report_at)} назад
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Sparkline истории цен */}
        {history && history.length >= 2 && (
          <div className="mt-5 p-3 bg-card rounded-xl border border-white/5">
            <div className="flex items-center justify-between mb-2">
              <div className="text-xs uppercase tracking-wider text-white/40 font-semibold">
                История цен · АИ-95
              </div>
              <div className="text-xs text-white/60 flex items-center gap-1">
                <span>{trendArrow(history.map((h) => h.price || 0))}</span>
                <span>
                  {history[history.length - 1]?.price}₽
                </span>
              </div>
            </div>
            <svg
              viewBox="0 0 200 40"
              preserveAspectRatio="none"
              style={{ width: "100%", height: 40 }}
            >
              <defs>
                <linearGradient id="spark" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#22c55e" stopOpacity="0.4" />
                  <stop offset="100%" stopColor="#22c55e" stopOpacity="0" />
                </linearGradient>
              </defs>
              <path
                d={`${sparklinePath(history.map((h) => h.price || 0), 200, 40)} L 200,40 L 0,40 Z`}
                fill="url(#spark)"
              />
              <path
                d={sparklinePath(history.map((h) => h.price || 0), 200, 40)}
                fill="none"
                stroke="#22c55e"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
        )}

        {/* Цены по источникам (новая фича) */}
        {prices && prices.total_sources > 0 && (
          <div className="mt-3 p-3 bg-card rounded-xl border border-white/5">
            <div className="text-xs uppercase tracking-wider text-white/40 font-semibold mb-2">
              Цены по источникам
              <span className="ml-2 text-white/30 normal-case font-normal">
                ({prices.total_sources} {prices.total_sources === 1 ? 'источник' : 'источников'})
              </span>
            </div>
            <div className="space-y-1.5">
              {Object.entries(prices.fuel_prices).map(([fuel, data]) => {
                if (!data.best) return null;
                const best = data.best;
                const ageStr = best.age_hours < 1
                  ? "только что"
                  : best.age_hours < 24
                    ? `${Math.round(best.age_hours)} ч назад`
                    : `${Math.round(best.age_hours / 24)} дн назад`;
                const sourceLabel: Record<string, string> = {
                  user: "👤 Водитель",
                  owner: "🏪 Владелец",
                  telegram: "✈️ Telegram",
                  yandex: "🌐 Яндекс",
                  lukoil: "🏢 Лукойл",
                  gazprom: "🏢 Газпромнефть",
                  rosneft: "🏢 Роснефть",
                  "2gis": "🗺 2ГИС",
                  osm: "🛣 OSM",
                };
                return (
                  <div key={fuel} className="text-sm flex items-center gap-2">
                    <span className="text-white/60 w-12">АИ-{fuel}</span>
                    <span className="font-semibold">{best.price}₽</span>
                    <span className="text-xs text-white/40">· {sourceLabel[best.source] || best.source}</span>
                    <span className="text-xs text-white/30 ml-auto">{ageStr}</span>
                  </div>
                );
              })}
            </div>
            {Object.values(prices.fuel_prices).some(d => d.all.length > 1) && (
              <div className="text-[10px] text-white/30 mt-2">
                💡 {Object.values(prices.fuel_prices).reduce((sum, d) => sum + d.all.length, 0)} отчётов из {prices.total_sources} источников — данные надёжные
              </div>
            )}
          </div>
        )}

        {/* Форма отчёта */}
        <div className="text-xs uppercase tracking-wider text-white/40 mb-2 font-semibold">
          Сообщить о наличии
        </div>

        <div className="flex gap-1.5 mb-3 overflow-x-auto scrollbar-hide">
          {["92", "95", "98", "diesel", "lpg"].map((fuel) => (
            <button
              key={fuel}
              onClick={() => setReportFuel(fuel)}
              className={`chip whitespace-nowrap ${
                reportFuel === fuel ? "chip-active" : "chip-inactive"
              }`}
            >
              {fuel === "diesel" ? "Дизель" : fuel === "lpg" ? "Газ" : `АИ-${fuel}`}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-3 gap-2 mb-3">
          <button
            onClick={() => submitReport(true)}
            className="btn bg-success/15 text-success hover:bg-success/25"
          >
            ✅ Есть
          </button>
          <button
            onClick={() => submitReport(null)}
            className="btn bg-warn/15 text-warn hover:bg-warn/25"
          >
            ⚠️ Мало
          </button>
          <button
            onClick={() => submitReport(false)}
            className="btn bg-danger/15 text-danger hover:bg-danger/25"
          >
            ❌ Нет
          </button>
        </div>

        <button
          onClick={() => setShowPriceInput((v) => !v)}
          className="text-xs text-white/40 hover:text-white/70 mb-2"
        >
          {showPriceInput ? "▾" : "▸"} Указать цену и очередь
        </button>

        {showPriceInput && (
          <div className="grid grid-cols-2 gap-2 mb-4" style={{ animation: "slideIn 0.2s" }}>
            <input
              type="number"
              inputMode="decimal"
              placeholder="Цена, ₽"
              value={reportPrice}
              onChange={(e) => setReportPrice(e.target.value)}
              className="bg-white/5 border border-white/10 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-accent/50"
              style={{ color: "#fff" }}
              step="0.01"
              min="0"
            />
            <input
              type="number"
              inputMode="numeric"
              placeholder="Очередь, авто"
              value={reportQueue}
              onChange={(e) => setReportQueue(e.target.value)}
              className="bg-white/5 border border-white/10 rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-accent/50"
              style={{ color: "#fff" }}
              min="0"
            />
          </div>
        )}

        {address === "—" && (
          <button
            onClick={() => {
              const url = `https://yandex.ru/maps/?pt=${station.lon},${station.lat}&z=18&l=map`;
              try {
                WebApp.openLink(url);
              } catch {
                window.open(url, "_blank");
              }
            }}
            className="w-full mt-2 btn bg-card hover:bg-white/10 text-sm"
          >
            🗺 Уточнить адрес на Яндекс.Картах
          </button>
        )}
      </div>
    </div>
  );
}
