// Типы данных от API

export type FuelType = "92" | "95" | "98" | "diesel" | "lpg" | "cng" | "electro" | "100";

export interface FuelStatus {
  fuel_type: string;
  available: boolean | null;
  price?: number | null;
  queue_size?: number | null;
  has_limit?: boolean;
  limit_liters?: number | null;
  confidence?: number;
  created_at?: string;
  last_report_at?: string;
}

export interface Station {
  id: number;
  name: string;
  operator?: string | null;
  city?: string | null;
  address?: string | null;
  region?: string | null;
  lat: number;
  lon: number;
  distance_km?: number;
  fuel_types?: string[];
  is_verified?: boolean;
  statuses: FuelStatus[];
  has_data: boolean;
}

export interface StationsResponse {
  stations: Station[];
  count: number;
}

export interface StationDetail {
  station: Station;
  statuses: FuelStatus[];
}
