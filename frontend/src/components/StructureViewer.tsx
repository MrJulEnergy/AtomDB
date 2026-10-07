import { createViewer, GLViewer } from "3dmol";
import { useEffect, useRef } from "react";
import { FrameData } from "../api";

interface Props {
  frame: FrameData;
  showForces: boolean;
  forceScale: number;
  style: "ballstick" | "spacefill" | "stick";
}

function toXYZ(f: FrameData): string {
  const lines = [String(f.n_atoms), f.formula];
  f.symbols.forEach((s, i) => lines.push(`${s} ${f.positions[i].join(" ")}`));
  return lines.join("\n");
}

/** 3D structure with unit cell and optional force arrows (3Dmol.js). */
export default function StructureViewer({ frame, showForces, forceScale, style }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const viewer = useRef<GLViewer | null>(null);
  const firstLoad = useRef(true);

  useEffect(() => {
    if (!ref.current) return;
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    if (!viewer.current) viewer.current = createViewer(ref.current, { backgroundColor: dark ? "#1e2124" : "#ffffff" });
    const v = viewer.current;
    v.removeAllModels();
    v.removeAllShapes();
    const m = v.addModel(toXYZ(frame), "xyz");
    const styles = {
      ballstick: { stick: { radius: 0.12 }, sphere: { scale: 0.28 } },
      spacefill: { sphere: { scale: 0.9 } },
      stick: { stick: { radius: 0.15 } },
    };
    m.setStyle({}, styles[style]);
    // unit cell edges
    const [a, b, c] = frame.cell;
    if (a && b && c && [a, b, c].some((x) => x.some((y) => y !== 0))) {
      const add = (p: number[], q: number[]) => p.map((x, i) => x + q[i]);
      const o = [0, 0, 0];
      const corners = [o, a, b, c, add(a, b), add(a, c), add(b, c), add(add(a, b), c)];
      const edges = [[0, 1], [0, 2], [0, 3], [1, 4], [1, 5], [2, 4], [2, 6], [3, 5], [3, 6], [4, 7], [5, 7], [6, 7]];
      for (const [i, j] of edges) {
        v.addLine({
          start: { x: corners[i][0], y: corners[i][1], z: corners[i][2] },
          end: { x: corners[j][0], y: corners[j][1], z: corners[j][2] },
          color: dark ? "#9097a0" : "#6b7075",
        });
      }
    }
    const forces: number[][] | undefined = frame.calc.forces ?? frame.arrays.forces;
    if (showForces && forces) {
      forces.forEach((fv, i) => {
        const p = frame.positions[i];
        const end = p.map((x, k) => x + fv[k] * forceScale);
        if (Math.hypot(...fv) * forceScale < 0.05) return;
        v.addArrow({
          start: { x: p[0], y: p[1], z: p[2] },
          end: { x: end[0], y: end[1], z: end[2] },
          radius: 0.05,
          color: "#d33",
        });
      });
    }
    if (firstLoad.current) {
      v.zoomTo();
      firstLoad.current = false;
    }
    v.render();
  }, [frame, showForces, forceScale, style]);

  useEffect(() => () => { viewer.current?.clear(); viewer.current = null; }, []);

  return <div ref={ref} className="viewer" />;
}
