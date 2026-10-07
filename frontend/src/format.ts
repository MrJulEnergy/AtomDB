export function fmtBytes(n: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function fmtNum(v: number | null | undefined, digits = 3): string {
  if (v === null || v === undefined) return "–";
  if (Number.isInteger(v)) return v.toLocaleString();
  return Math.abs(v) >= 1e4 || (Math.abs(v) < 1e-2 && v !== 0) ? v.toExponential(digits) : v.toFixed(digits);
}

export function fmtDate(iso: string): string {
  return iso.slice(0, 10);
}

export function fmtRange(a: number | null, b: number | null, digits = 3): string {
  if (a === null || b === null) return "–";
  return a === b ? fmtNum(a, digits) : `${fmtNum(a, digits)} … ${fmtNum(b, digits)}`;
}
