// Thin typed client for /api/v1. Types mirror atomdb.schemas / services.

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
  extra: Record<string, string[]>;
}

export interface DatasetDetail extends DatasetSummary {
  description: string | null;
  updated_at: string;
  source_text: string | null;
  files: { role: string; file: FileOut }[];
  notes: string[];
}

export interface SearchResult {
  query: string;
  interpreted: string[];
  total: number;
  offset: number;
  limit: number;
  items: DatasetSummary[];
}

export interface Page<T> {
  total: number;
  offset: number;
  limit: number;
  items: T[];
}

export interface NodeBrief {
  id: string;
  kind: string;
  name: string;
  status: string;
  subtitle: string;
  calc_type?: string;
  level?: number;
}

// Full node description (fields depend on kind)
export type NodeDesc = NodeBrief & Record<string, any>;

export interface GraphEdge {
  src: string;
  dst: string;
  relation: string;
  role: string;
  source: string;
  attrs: Record<string, unknown> | null;
}

export interface Graph {
  root: string;
  direction: "up" | "down" | "both";
  nodes: NodeBrief[];
  edges: GraphEdge[];
}

export interface FrameRow {
  idx: number;
  n_atoms: number;
  formula: string;
  energy: number | null;
  energy_per_atom: number | null;
  fmax: number | null;
  temperature: number | null;
  volume: number | null;
  time: number | null;
  step: number | null;
}

export interface FrameData {
  dataset: string;
  index: number;
  n_atoms: number;
  formula: string;
  symbols: string[];
  numbers: number[];
  positions: number[][];
  cell: number[][];
  pbc: boolean[];
  energy: number | null;
  energy_per_atom: number | null;
  fmax: number | null;
  temperature: number | null;
  temperature_source: string | null;
  volume: number | null;
  time: number | null;
  step: number | null;
  calc: Record<string, any>;
  info: Record<string, any>;
  arrays: Record<string, any>;
}

export interface LineageStep {
  dataset: string;
  dataset_name: string | null;
  frame: number;
  via: string | null;
  via_name?: string | null;
}

export interface Completeness {
  dataset: string;
  complete: boolean;
  score: number;
  checks: { item: string; ok: boolean; hint: string | null }[];
  missing: string[];
}

export interface PropertyDef {
  origin: string;
  name: string;
  level: string;
  dtype: string;
  shape: number[] | null;
  unit: string | null;
  count: number;
  label_calc_id: string | null;
}

export interface ElementCount {
  symbol: string;
  z: number;
  datasets: number;
  frames: number;
  atoms: number;
}

export class ApiError extends Error {
  constructor(public code: string, message: string) {
    super(message);
  }
}

type Params = Record<string, string | number | boolean | undefined | string[]>;

export async function get<T>(path: string, params?: Params): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => qs.append(k, x));
    else qs.set(k, String(v));
  }
  const res = await fetch(`/api/v1${path}${qs.toString() ? `?${qs}` : ""}`);
  const body = await res.json();
  if (!res.ok) throw new ApiError(body?.error?.code ?? "error", body?.error?.message ?? res.statusText);
  return body as T;
}

export function nodeHref(id: string): string {
  return id.startsWith("DS-") ? `/datasets/${id}` : `/nodes/${id}`;
}
