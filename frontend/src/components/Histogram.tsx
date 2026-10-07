import { useMemo, useRef, useState } from "react";

export interface Series {
  label: string;
  /** palette slot 1..8 — assigned by entity, in fixed order */
  slot: number;
  edges: number[];
  counts: number[];
}

interface Props {
  series: Series[];
  xLabel: string;
  title?: string;
  normalize?: boolean;
  logY?: boolean;
  height?: number;
}

const W = 360;
const M = { l: 44, r: 8, t: 6, b: 30 };

function ticks(lo: number, hi: number, n = 4): number[] {
  if (!(hi > lo)) return [lo];
  const step = Math.pow(10, Math.floor(Math.log10((hi - lo) / n)));
  const err = (hi - lo) / n / step;
  const s = step * (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1);
  const out = [];
  for (let v = Math.ceil(lo / s) * s; v <= hi + 1e-9 * s; v += s) out.push(Number(v.toPrecision(12)));
  return out;
}

function fmt(v: number): string {
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(1);
  return Number(v.toPrecision(4)).toString();
}

/** Step-line histogram(s) on one shared axis; hover shows each series' value in the bin. */
export default function Histogram({ series, xLabel, title, normalize = false, logY = false, height = 170 }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<SVGSVGElement>(null);
  const H = height;
  const data = useMemo(
    () =>
      series.map((s) => {
        const tot = s.counts.reduce((a, b) => a + b, 0) || 1;
        return { ...s, y: s.counts.map((c) => (normalize ? c / tot : c)) };
      }),
    [series, normalize],
  );
  if (!data.length || !data[0].edges.length) return null;
  const edges = data[0].edges;
  const x0 = edges[0];
  const x1 = edges[edges.length - 1];
  const ymaxRaw = Math.max(...data.flatMap((d) => d.y), 1e-12);
  const ymin = logY ? Math.max(Math.min(...data.flatMap((d) => d.y.filter((v) => v > 0)), ymaxRaw) / 2, 1e-12) : 0;
  const ymax = logY ? ymaxRaw * 1.5 : ymaxRaw * 1.05;
  const sx = (v: number) => M.l + ((v - x0) / (x1 - x0 || 1)) * (W - M.l - M.r);
  const sy = (v: number) => {
    if (logY) {
      const lv = Math.log10(Math.max(v, ymin));
      return H - M.b - ((lv - Math.log10(ymin)) / (Math.log10(ymax) - Math.log10(ymin))) * (H - M.t - M.b);
    }
    return H - M.b - (v / ymax) * (H - M.t - M.b);
  };
  const path = (y: number[]) => {
    let d = `M${sx(edges[0])},${sy(logY ? ymin : 0)}`;
    y.forEach((v, i) => {
      d += `L${sx(edges[i])},${sy(v)}L${sx(edges[i + 1])},${sy(v)}`;
    });
    return d + `L${sx(edges[edges.length - 1])},${sy(logY ? ymin : 0)}`;
  };
  const xt = ticks(x0, x1);
  const yt = logY ? [] : ticks(0, ymax, 3).filter((v) => normalize || Number.isInteger(v));
  const onMove = (e: React.MouseEvent) => {
    const r = ref.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const v = x0 + ((px - M.l) / (W - M.l - M.r)) * (x1 - x0);
    const i = edges.findIndex((ed, k) => k < edges.length - 1 && v >= ed && v < edges[k + 1]);
    setHover(i >= 0 ? i : null);
  };
  return (
    <div className="chart">
      {title && <p className="title">{title}</p>}
      {data.length > 1 && (
        <div className="legend-row">
          {data.map((d) => (
            <span key={d.label}><i style={{ background: `var(--series-${d.slot})` }} />{d.label}</span>
          ))}
        </div>
      )}
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img" aria-label={`${title ?? ""} histogram of ${xLabel}`}>
        {yt.map((t) => (
          <g key={t} className="axis">
            <line className="gridline" x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
            <text x={M.l - 4} y={sy(t) + 3} textAnchor="end">{fmt(t)}</text>
          </g>
        ))}
        <g className="axis">
          <line x1={M.l} x2={W - M.r} y1={H - M.b} y2={H - M.b} />
          {xt.map((t) => (
            <text key={t} x={sx(t)} y={H - M.b + 12} textAnchor="middle">{fmt(t)}</text>
          ))}
          <text x={(M.l + W - M.r) / 2} y={H - 4} textAnchor="middle">{xLabel}</text>
          <text x={10} y={(H - M.b) / 2} textAnchor="middle" transform={`rotate(-90 10 ${(H - M.b) / 2})`}>
            {normalize ? "fraction" : "count"}{logY ? " (log)" : ""}
          </text>
        </g>
        {hover !== null && (
          <rect x={sx(edges[hover])} width={Math.max(1, sx(edges[hover + 1]) - sx(edges[hover]))} y={M.t} height={H - M.t - M.b} fill="var(--accent-weak)" />
        )}
        {data.map((d) => (
          <path key={d.label} d={path(d.y)} fill={data.length === 1 ? `var(--series-${d.slot})` : "none"} fillOpacity={data.length === 1 ? 0.15 : 0}
            stroke={`var(--series-${d.slot})`} strokeWidth={2} strokeLinejoin="round" />
        ))}
      </svg>
      {hover !== null && (
        <div className="tip" style={{ left: `${Math.min(70, (sx(edges[hover]) / W) * 100)}%`, top: 24 }}>
          <div>{fmt(edges[hover])} … {fmt(edges[hover + 1])}</div>
          {data.map((d) => (
            <div key={d.label}>
              <i style={{ background: `var(--series-${d.slot})` }} />
              {data.length > 1 ? `${d.label}: ` : ""}
              {normalize ? (d.y[hover] * 100).toFixed(1) + " %" : d.counts[hover].toLocaleString()}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
