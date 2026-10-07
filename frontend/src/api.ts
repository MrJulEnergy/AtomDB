// Thin typed client for /api/v1. Types mirror atomdb.schemas.

export interface FileOrigin {
  original_path: string;
  original_name: string;
  hostname: string | null;
  mtime: string | null;
  imported_at: string;
}

export interface FileOut {
  id: string;
  name: string;
  sha256: string;
  size_bytes: number;
  format: string | null;
  store_path: string;
  origins: FileOrigin[];
}

export interface DatasetSummary {
  id: string;
  name: string;
  status: string;
  created_at: string;
  n_frames: number | null;
  n_atoms_min: number | null;
  n_atoms_max: number | null;
  formula_summary: string | null;
  elements: string[];
  has_energy: boolean;
  has_forces: boolean;
  has_stress: boolean;
  energy_min: number | null;
  energy_max: number | null;
  fmax_max: number | null;
  tags: string[];
  extra: Record<string, unknown>;
}

export interface DatasetDetail extends DatasetSummary {
  description: string | null;
  updated_at: string;
  source_text: string | null;
  files: { role: string; file: FileOut }[];
  notes: string[];
}

export interface Page<T> {
  total: number;
  offset: number;
  limit: number;
  items: T[];
}

export class ApiError extends Error {
  constructor(public code: string, message: string) {
    super(message);
  }
}

export async function get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v !== undefined && v !== "") qs.set(k, String(v));
  const res = await fetch(`/api/v1${path}${qs.toString() ? `?${qs}` : ""}`);
  const body = await res.json();
  if (!res.ok) throw new ApiError(body?.error?.code ?? "error", body?.error?.message ?? res.statusText);
  return body as T;
}
