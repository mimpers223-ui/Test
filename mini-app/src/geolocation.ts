// Геолокация: 3 уровня fallback

export interface LocationResult {
  lat: number;
  lon: number;
  source: "gps" | "ip" | "manual" | "default";
  accuracy?: number;
}

export async function getUserLocation(): Promise<LocationResult> {
  // 1. Браузерный API (Telegram WebView)
  if (navigator.geolocation) {
    try {
      const pos = await new Promise<GeolocationPosition>((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 10000,
          maximumAge: 60000,
        });
      });
      return {
        lat: pos.coords.latitude,
        lon: pos.coords.longitude,
        source: "gps",
        accuracy: pos.coords.accuracy,
      };
    } catch (e) {
      console.warn("Browser geolocation failed:", e);
    }
  }

  // 2. IP-геолокация (fallback, ~точность до города)
  try {
    const res = await fetch("https://ipapi.co/json/");
    if (res.ok) {
      const data = await res.json();
      if (data.latitude && data.longitude) {
        return {
          lat: data.latitude,
          lon: data.longitude,
          source: "ip",
        };
      }
    }
  } catch (e) {
    console.warn("IP geolocation failed:", e);
  }

  // 3. Москва по умолчанию
  return { lat: 55.7558, lon: 37.6173, source: "default" };
}

// Получить только GPS (для кнопки "Где я")
export function getGPSLocation(): Promise<LocationResult> {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("Геолокация не поддерживается"));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        resolve({
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          source: "gps",
          accuracy: pos.coords.accuracy,
        }),
      (err) => {
        const messages: Record<number, string> = {
          1: "Разреши доступ к геолокации в настройках Telegram",
          2: "Геолокация недоступна",
          3: "Таймаут. Попробуй ещё раз",
        };
        reject(new Error(messages[err.code] || err.message));
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 30000 },
    );
  });
}
