import { useEffect, useRef, useState } from "react";

export interface Point {
  x: number;
  y: number;
  /** categorical slot (1..3) or null when colored by value */
  slot: number | null;
  value: number | null;
  label: string;
  key: string;
}

interface Props {
  points: Point[];
  xLabel: string;
  yLabel: string;
  valueRange?: [number, number];
  onPick: (key: string) => void;
}

const H = 520;
const PAD = { l: 48, r: 12, t: 10, b: 36 };

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Canvas scatter for thousands of points; nearest-point hover tooltip, click to open. */
export default function Scatter({ points, xLabel, yLabel, valueRange, onPick }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ p: Point; px: number; py: number } | null>(null);
  const [width, setWidth] = useState(800);

  useEffect(() => {
    const ro = new ResizeObserver((e) => setWidth(e[0].contentRect.width));
    if (wrap.current) ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const pad = (lo: number, hi: number) => {
    const d = hi - lo || 1;
    return [lo - d * 0.05, hi + d * 0.05];
  };
  const [x0, x1] = pad(Math.min(...xs, 0), Math.max(...xs, 0));
  const [y0, y1] = pad(Math.min(...ys, 0), Math.max(...ys, 0));
  const sx = (v: number) => PAD.l + ((v - x0) / (x1 - x0)) * (width - PAD.l - PAD.r);
  const sy = (v: number) => H - PAD.b - ((v - y0) / (y1 - y0)) * (H - PAD.t - PAD.b);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr;
    c.height = H * dpr;
    const g = c.getContext("2d")!;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, width, H);
    g.strokeStyle = cssVar("--grid");
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(sx(0), PAD.t);
    g.lineTo(sx(0), H - PAD.b);
    g.moveTo(PAD.l, sy(0));
    g.lineTo(width - PAD.r, sy(0));
    g.stroke();
    g.fillStyle = cssVar("--muted");
    g.font = "11px system-ui";
    g.textAlign = "center";
    g.fillText(xLabel, (PAD.l + width - PAD.r) / 2, H - 8);
    g.save();
    g.translate(14, (H - PAD.b) / 2);
    g.rotate(-Math.PI / 2);
    g.fillText(yLabel, 0, 0);
    g.restore();
    const seq = ["--seq-100", "--seq-200", "--seq-300", "--seq-400", "--seq-500", "--seq-600"].map(cssVar);
    const surface = cssVar("--panel");
    const [v0, v1] = valueRange ?? [0, 1];
    for (const p of points) {
      let col: string;
      if (p.slot !== null) col = cssVar(`--series-${p.slot}`);
      else if (p.value === null) col = cssVar("--dim");
      else col = seq[Math.min(5, Math.max(0, Math.floor(((p.value - v0) / (v1 - v0 || 1)) * 6)))];
      g.beginPath();
      g.arc(sx(p.x), sy(p.y), 4, 0, 2 * Math.PI);
      g.fillStyle = col;
      g.fill();
      g.lineWidth = 1.5; // surface ring separates overlapping marks
      g.strokeStyle = surface;
      g.stroke();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, width, valueRange]);

  const nearest = (e: React.MouseEvent) => {
    const r = canvas.current!.getBoundingClientRect();
    const mx = e.clientX - r.left;
    const my = e.clientY - r.top;
    let best: Point | null = null;
    let bd = 12 * 12;
    for (const p of points) {
      const d = (sx(p.x) - mx) ** 2 + (sy(p.y) - my) ** 2;
      if (d < bd) {
        bd = d;
        best = p;
      }
    }
    return best ? { p: best, px: sx(best.x), py: sy(best.y) } : null;
  };

  return (
    <div className="chart" ref={wrap}>
      <canvas ref={canvas} style={{ width: "100%", height: H, cursor: hover ? "pointer" : "default" }}
        onMouseMove={(e) => setHover(nearest(e))} onMouseLeave={() => setHover(null)}
        onClick={(e) => { const n = nearest(e); if (n) onPick(n.p.key); }} />
      {hover && (
        <div className="tip" style={{ left: Math.min(hover.px + 10, width - 220), top: Math.max(0, hover.py - 10) }}>
          {hover.p.label.split("\n").map((l) => <div key={l}>{l}</div>)}
          <div className="muted">click to open frame</div>
        </div>
      )}
    </div>
  );
}
