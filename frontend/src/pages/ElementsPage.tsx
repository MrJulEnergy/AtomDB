import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ElementCount, SearchResult, get } from "../api";
import { ErrorBox, Loading, NodeLink } from "../components/common";

const SYMBOLS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr " +
  "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os " +
  "Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og").split(" ");

/** (row, column) in the 18-column layout; rows 9/10 hold lanthanides/actinides. */
function position(z: number): [number, number] {
  if (z === 1) return [1, 1];
  if (z === 2) return [1, 18];
  const blocks: [number, number, number][] = [[3, 2, 0], [11, 3, 0], [19, 4, 0], [37, 5, 0], [55, 6, 0], [87, 7, 0]];
  if (z >= 57 && z <= 71) return [9, z - 57 + 3];
  if (z >= 89 && z <= 103) return [10, z - 89 + 3];
  for (let i = blocks.length - 1; i >= 0; i--) {
    const [start, row] = blocks[i];
    if (z >= start) {
      const k = z - start;
      if (row <= 3) return [row, k < 2 ? k + 1 : k + 11];
      if (row <= 5) return [row, k + 1];
      if (k < 2) return [row, k + 1];
      return [row, z - (row === 6 ? 72 : 104) + 4];
    }
  }
  return [0, 0];
}

const STEPS = ["--seq-100", "--seq-200", "--seq-300"];

export default function ElementsPage() {
  const [params, setParams] = useSearchParams();
  const sel = params.getAll("el");
  const mode = params.get("mode") === "or" ? "or" : "and";
  const els = useQuery({ queryKey: ["elements"], queryFn: () => get<{ n_datasets: number; elements: ElementCount[] }>("/elements") });
  const byZ = useMemo(() => new Map((els.data?.elements ?? []).map((e) => [e.z, e])), [els.data]);
  const maxDs = Math.max(1, ...(els.data?.elements ?? []).map((e) => e.datasets));
  const res = useQuery({
    queryKey: ["el-search", sel, mode],
    queryFn: () => get<SearchResult>("/search", { element: sel, any: mode === "or", limit: 500 }),
    enabled: sel.length > 0,
  });
  const frames = useQuery({
    queryKey: ["el-frames", sel, mode],
    queryFn: () => get<{ total: number; per_dataset: Record<string, number> }>("/frames", mode === "or" ? { any: sel, limit: 1 } : { all: sel, limit: 1 }),
    enabled: sel.length > 0,
  });
  const toggle = (s: string) => {
    const next = sel.includes(s) ? sel.filter((x) => x !== s) : [...sel, s];
    const p = new URLSearchParams();
    next.forEach((x) => p.append("el", x));
    if (mode === "or") p.set("mode", "or");
    setParams(p);
  };
  const setMode = (m: string) => {
    const p = new URLSearchParams(params);
    m === "or" ? p.set("mode", "or") : p.delete("mode");
    setParams(p);
  };
  const step = (n: number) => STEPS[Math.min(STEPS.length - 1, Math.floor((Math.log(n) / Math.log(maxDs + 1)) * STEPS.length))];
  const query = mode === "or" ? `el:${sel.join("|")}` : `el:${sel.join(",")}`;

  return (
    <section>
      <div className="page-head">
        <h1>Elements in the database</h1>
        <div className="toggles">
          <button className={mode === "and" ? "btn primary" : "btn"} onClick={() => setMode("and")}>contains ALL selected</button>
          <button className={mode === "or" ? "btn primary" : "btn"} onClick={() => setMode("or")}>contains ANY selected</button>
          {sel.length > 0 && <button className="btn" onClick={() => setParams({})}>clear</button>}
        </div>
      </div>
      {els.error && <ErrorBox error={els.error} />}
      <div className="ptable" role="grid" aria-label="periodic table">
        {SYMBOLS.map((s, i) => {
          const z = i + 1;
          const [r, c] = position(z);
          const e = byZ.get(z);
          const cls = `pcell${e ? "" : " absent"}${sel.includes(s) ? " sel" : ""}`;
          return (
            <button key={s} className={cls} style={{ gridRow: r, gridColumn: c, background: e ? `var(${step(e.datasets)})` : undefined }}
              disabled={!e} onClick={() => toggle(s)}
              title={e ? `${s} (Z=${z}): ${e.datasets} datasets, ${e.frames.toLocaleString()} frames, ${e.atoms.toLocaleString()} atoms` : `${s}: not in database`}>
              {s}
              <small>{e ? e.datasets : z}</small>
            </button>
          );
        })}
        <div className="pcell gap" style={{ gridRow: 8, gridColumn: 1 }} />
      </div>
      <div className="ptable-legend">
        datasets containing the element:
        {STEPS.map((s, i) => <span key={s} className="sw" style={{ background: `var(${s})` }} title={`step ${i + 1}`} />)}
        few → many (log) · dashed = absent
      </div>
      {sel.length > 0 && (
        <>
          <h2>
            {mode === "and" ? "Contains " + sel.join(" + ") : "Contains any of " + sel.join(", ")} —{" "}
            {res.data?.total ?? "…"} datasets, {frames.data?.total.toLocaleString() ?? "…"} frames{" "}
            <Link className="small" to={`/?q=${encodeURIComponent(query)}`}>open in explorer →</Link>
          </h2>
          {res.isLoading && <Loading />}
          {res.data && (
            <table className="grid">
              <thead><tr><th>ID</th><th>Name</th><th>Elements</th><th className="num">Matching frames</th><th className="num">Frames</th><th>Functional</th><th>MLIP</th></tr></thead>
              <tbody>
                {res.data.items.map((d) => (
                  <tr key={d.id}>
                    <td><NodeLink id={d.id} /></td>
                    <td className="wrap name">{d.name}</td>
                    <td>{d.elements.join(" ")}</td>
                    <td className="num">{frames.data?.per_dataset[d.id] !== undefined ? <Link to={`/datasets/${d.id}`}>{frames.data.per_dataset[d.id]}</Link> : "–"}</td>
                    <td className="num">{d.n_frames}</td>
                    <td>{(d.extra.functional ?? []).join(", ")}</td>
                    <td>{(d.extra.model ?? []).join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}
