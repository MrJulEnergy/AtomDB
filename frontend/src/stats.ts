export interface Quantity {
  unit: string;
  level: string;
  count: number;
  min?: number;
  max?: number;
  mean?: number;
  std?: number;
  hist?: { edges: number[]; counts: number[] };
}

export interface DatasetStats {
  dataset: string;
  name: string;
  n_frames: number;
  quantities: Record<string, Quantity>;
  force_per_element: Record<string, Quantity>;
  composition: Record<string, number>;
}

export const LABELS: Record<string, string> = {
  energy: "Energy",
  energy_per_atom: "Energy / atom",
  fmax: "max |F| per frame",
  force_norm: "|F| (all atoms)",
  force_x: "Fx",
  force_y: "Fy",
  force_z: "Fz",
  n_atoms: "Atoms per frame",
  volume: "Cell volume",
  density: "Density",
  temperature: "Temperature",
  cell_a: "a",
  cell_b: "b",
  cell_c: "c",
  cell_alpha: "α",
  cell_beta: "β",
  cell_gamma: "γ",
};

export const GROUPS: [string, string[]][] = [
  ["Energy", ["energy", "energy_per_atom"]],
  ["Forces", ["force_norm", "fmax", "force_x", "force_y", "force_z"]],
  ["Structure", ["n_atoms", "volume", "density", "temperature", "cell_a", "cell_b", "cell_c", "cell_alpha", "cell_beta", "cell_gamma"]],
];

export function num(v: number | undefined, d = 4): string {
  if (v === undefined || v === null) return "–";
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(2);
  return Number(v.toPrecision(d + 1)).toString();
}
