import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { DatasetDetail, FrameData, LineageStep, get } from "../api";
import { ErrorBox, KV, Loading, NodeLink, Panel, ProvButtons } from "../components/common";
import ProvSummary from "../components/ProvSummary";
import StructureViewer from "../components/StructureViewer";
import { fmtNum } from "../format";

function short(v: unknown): string {
  if (Array.isArray(v)) {
    const flat = (v as unknown[]).flat(2);
    return flat.length > 9 ? `[${flat.length} values]` : JSON.stringify(v);
  }
  if (typeof v === "number") return fmtNum(v, 5);
  if (typeof v === "object" && v !== null) return JSON.stringify(v);
  return String(v);
}

export default function FramePage() {
  const { id, idx } = useParams();
  const i = Number(idx);
  const navigate = useNavigate();
  const [showForces, setShowForces] = useState(false);
  const [forceScale, setForceScale] = useState(1);
  const [style, setStyle] = useState<"ballstick" | "spacefill" | "stick">("ballstick");
  const ds = useQuery({ queryKey: ["dataset", id], queryFn: () => get<DatasetDetail>(`/datasets/${id}`) });
  const fr = useQuery({ queryKey: ["frame", id, i], queryFn: () => get<FrameData>(`/datasets/${id}/frames/${i}`), placeholderData: (p) => p });
  const lin = useQuery({ queryKey: ["lineage", id, i], queryFn: () => get<LineageStep[]>(`/datasets/${id}/frames/${i}/lineage`) });
  const n = ds.data?.n_frames ?? 0;
  const go = (k: number) => navigate(`/datasets/${id}/frames/${Math.max(0, Math.min(n - 1, k))}`, { replace: true });

  if (fr.error) return <ErrorBox error={fr.error} />;
  if (!fr.data) return <Loading what="frame" />;
  const f = fr.data;
  const forces: number[][] | undefined = f.calc.forces ?? f.arrays.forces;
  const stress: number[] | undefined = f.calc.stress;
  return (
    <section>
      <div className="page-head">
        <h1>
          <NodeLink id={id!} /> {ds.data?.name} · frame {i}
        </h1>
        <ProvButtons id={id!} />
      </div>
      <div className="frame-nav">
        <button onClick={() => go(0)}>⏮</button>
        <button onClick={() => go(i - 1)}>‹</button>
        <input type="range" min={0} max={Math.max(0, n - 1)} value={i} onChange={(e) => go(Number(e.target.value))} />
        <button onClick={() => go(i + 1)}>›</button>
        <button onClick={() => go(n - 1)}>⏭</button>
        <span className="muted">frame {i} of {n} (0-based)</span>
        <select value={style} onChange={(e) => setStyle(e.target.value as typeof style)}>
          <option value="ballstick">ball &amp; stick</option>
          <option value="spacefill">spacefill</option>
          <option value="stick">stick</option>
        </select>
        {forces && (
          <label className="small">
            <input type="checkbox" checked={showForces} onChange={(e) => setShowForces(e.target.checked)} /> forces ×
            <input className="scale" type="number" step={0.5} min={0.1} value={forceScale} onChange={(e) => setForceScale(Number(e.target.value))} /> Å/(eV/Å)
          </label>
        )}
      </div>
      <div className="frame-layout">
        <StructureViewer frame={f} showForces={showForces} forceScale={forceScale} style={style} />
        <div className="panels single">
          <Panel title="Frame">
            <KV rows={[
              ["index", i], ["formula", f.formula], ["atoms", f.n_atoms],
              ["energy", f.energy !== null ? `${fmtNum(f.energy, 6)} eV` : null],
              ["energy / atom", f.energy_per_atom !== null ? `${fmtNum(f.energy_per_atom, 6)} eV` : null],
              ["max |F|", f.fmax !== null ? `${fmtNum(f.fmax, 4)} eV/Å` : null],
              ["temperature", f.temperature !== null ? `${fmtNum(f.temperature, 2)} K (${f.temperature_source})` : null],
              ["volume", f.volume !== null ? `${fmtNum(f.volume, 2)} Å³` : null],
              ["time", f.time !== null ? `${fmtNum(f.time, 2)} fs` : null], ["step", f.step],
              ["pbc", f.pbc.map((x) => (x ? "T" : "F")).join(" ")],
              ["cell [Å]", <pre className="code small">{f.cell.map((r) => r.map((x) => x.toFixed(4).padStart(10)).join(" ")).join("\n")}</pre>],
              ["stress [eV/Å³]", stress ? <span className="mono small">{stress.map((x) => x.toExponential(3)).join(" ")}</span> : null],
            ]} />
          </Panel>
          <Panel title="Frame lineage">
            {lin.data && lin.data.length > 1 ? (
              <ol className="lineage">
                {lin.data.map((s, k) => (
                  <li key={k}>
                    <Link to={`/datasets/${s.dataset}/frames/${s.frame}`}>{s.dataset} frame {s.frame}</Link> <span className="muted">{s.dataset_name}</span>
                    {s.via && <div className="small muted">↑ via <NodeLink id={s.via} /> {s.via_name}</div>}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="muted">No frame-level links (frame originates in this dataset or the link is unknown).</p>
            )}
          </Panel>
          <Panel title="How was this dataset made?">
            <ProvSummary id={id!} />
          </Panel>
          {Object.keys(f.info).length > 0 && (
            <Panel title="Per-frame metadata (info)">
              <KV rows={Object.entries(f.info).map(([k, v]) => [k, short(v)] as [string, string])} />
            </Panel>
          )}
          {Object.keys(f.calc).length > 0 && (
            <Panel title="Calculator results">
              <KV rows={Object.entries(f.calc).map(([k, v]) => [k, short(v)] as [string, string])} />
            </Panel>
          )}
        </div>
      </div>
      <Panel title="Atoms" wide>
        <div className="scroll-box">
          <table className="grid compact">
            <thead>
              <tr>
                <th className="num">#</th><th>El.</th><th className="num">x</th><th className="num">y</th><th className="num">z</th>
                {forces && <><th className="num">Fx</th><th className="num">Fy</th><th className="num">Fz</th><th className="num">|F|</th></>}
                {Object.keys(f.arrays).filter((k) => k !== "forces").map((k) => <th key={k}>{k}</th>)}
              </tr>
            </thead>
            <tbody>
              {f.symbols.map((s, a) => (
                <tr key={a}>
                  <td className="num">{a}</td><td>{s}</td>
                  {f.positions[a].map((x, k) => <td key={k} className="num">{x.toFixed(4)}</td>)}
                  {forces && (
                    <>
                      {forces[a].map((x, k) => <td key={k} className="num">{x.toFixed(4)}</td>)}
                      <td className="num">{Math.hypot(...forces[a]).toFixed(4)}</td>
                    </>
                  )}
                  {Object.entries(f.arrays).filter(([k]) => k !== "forces").map(([k, v]) => <td key={k} className="mono small">{short((v as unknown[])[a])}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </section>
  );
}
