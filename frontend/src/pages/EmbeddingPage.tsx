import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { DatasetSummary, SearchResult, get } from "../api";
import { ErrorBox, Loading, NodeLink } from "../components/common";
import DsName from "../components/DsName";
import Scatter, { Point } from "../components/Scatter";

interface EmbPoint {
  dataset: string; frame: number; pc1: number; pc2: number;
  atom?: number; element?: string;
  energy_per_atom?: number | null; fmax?: number | null; n_atoms?: number | null; temperature?: number | null;
}
interface EmbDataset { id: string; name: string; label: string | null; n_frames: number; n_atoms?: number; elements?: Record<string, number> }
interface Embedding {
  note: string;
  descriptor: string;
  n_features: number;
  explained_variance: number[];
  cache?: { frames_cached: number; frames_computed: number };
  datasets: EmbDataset[];
  skipped?: { id: string; name: string; reason: string }[];
  points: EmbPoint[];
}
interface Similarity {
  reference: string; other: string; novel_fraction: number; n_other_frames: number; novelty_threshold: number; verdict: string;
  composition: { elements_only_in_other: string[]; elements_only_in_reference: string[] };
  distributions: Record<string, { mean_shift_std: number; hist_overlap: number | null }>;
  most_novel_frames: { dataset: string; frame: number; nn_distance: number }[];
}
interface DescriptorInfo { name: string; available: boolean; description: string; per_atom: boolean }

type Mode = "frames" | "atoms";
const FRAME_COLOR = ["dataset", "energy_per_atom", "fmax", "n_atoms", "temperature"] as const;
const ATOM_COLOR = ["dataset", "element"] as const;
type ColorBy = (typeof FRAME_COLOR)[number] | (typeof ATOM_COLOR)[number];
const MAX_CATEGORICAL = 3; // scatter = all-pairs comparison: only 3 palette slots validate (light + dark)
const SEQ = ["--seq-100", "--seq-200", "--seq-300", "--seq-400", "--seq-500", "--seq-600"];
// same thresholds as services/embedding.is_large
const isLarge = (d: DatasetSummary) => (d.n_frames ?? 0) > 100_000 || (d.n_atoms_max ?? 0) > 5_000;

const Swatch = ({ v }: { v: string }) => <i style={{ background: `var(${v})`, height: 8, width: 8, borderRadius: 4, display: "inline-block", marginRight: 4 }} />;

export default function EmbeddingPage() {
  const [params, setParams] = useSearchParams();
  const mode: Mode = params.get("mode") === "atoms" ? "atoms" : "frames";
  const ids = params.getAll("ds");
  const elFilter = params.getAll("el");
  const allowLarge = params.get("large") === "1";
  const maxFrames = Number(params.get("max") ?? (mode === "atoms" ? 200 : 300));
  const maxAtoms = Number(params.get("maxatoms") ?? 5000);
  const navigate = useNavigate();

  const descs = useQuery({ queryKey: ["descriptors"], queryFn: () => get<DescriptorInfo[]>("/descriptors") });
  const modeDescs = (descs.data ?? []).filter((d) => d.per_atom === (mode === "atoms"));
  const descriptor = params.get("descriptor") ?? (mode === "atoms" ? "mace-mp0-small" : "composition-rdf");

  // every dataset (with label + chips) for the picker; the store is small enough to list in full
  const all = useQuery({ queryKey: ["search", "", "all"], queryFn: () => get<SearchResult>("/search", { q: "", limit: 1000, sort: "id" }), staleTime: 60_000 });
  const byId = useMemo(() => new Map((all.data?.items ?? []).map((d) => [d.id, d])), [all.data]);
  const [picked, setPicked] = useState<string[]>(ids);
  const [filter, setFilter] = useState("");
  const [colorBy, setColorBy] = useState<ColorBy>("dataset");
  const [split, setSplit] = useState(false);
  const [opacity, setOpacity] = useState(1);
  const [top, setTop] = useState<string>(""); // "" = API order, "mix" = interleaved, else dataset id drawn last

  const q = useQuery({
    queryKey: ["embedding", mode, ids, descriptor, maxFrames, maxAtoms, elFilter, allowLarge],
    queryFn: () => mode === "atoms"
      ? get<Embedding>("/embedding/atoms", { ds: ids, descriptor, max_frames: maxFrames, max_atoms: maxAtoms, element: elFilter, allow_large: allowLarge })
      : get<Embedding>("/embedding", { ds: ids, descriptor, max_frames: maxFrames }),
    enabled: ids.length > 0 && (descs.data === undefined || modeDescs.some((d) => d.name === descriptor)),
    staleTime: Infinity,
    retry: false,
  });
  const sim = useQuery({
    queryKey: ["similarity", ids[0], ids[1], descriptor],
    queryFn: () => get<Similarity>("/similarity", { reference: ids[0], other: ids[1], descriptor }),
    enabled: mode === "frames" && ids.length === 2,
    staleTime: Infinity,
  });

  const setMany = (next: Record<string, string | string[] | null>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      p.delete(k);
      if (Array.isArray(v)) v.forEach((x) => p.append(k, x));
      else if (v !== null && v !== "") p.set(k, v);
    }
    setParams(p);
  };
  const setMode = (m: Mode) => { setColorBy("dataset"); setMany({ mode: m, descriptor: null, max: null }); };

  const ds = q.data?.datasets ?? [];
  const elements = useMemo(() => [...new Set(ids.flatMap((i) => byId.get(i)?.elements ?? []))].sort(), [ids, byId]);
  const shownElements = useMemo(() => [...new Set((q.data?.points ?? []).map((p) => p.element).filter(Boolean) as string[])].sort(), [q.data]);
  const colorOptions: readonly ColorBy[] = mode === "atoms" ? ATOM_COLOR : FRAME_COLOR;
  const tooMany = (c: ColorBy) => (c === "dataset" && ds.length > MAX_CATEGORICAL) || (c === "element" && shownElements.length > MAX_CATEGORICAL);
  const effective: ColorBy = !colorOptions.includes(colorBy) ? "dataset" : colorBy;
  // more series than validated slots → one panel per dataset instead of more hues
  const multiples = split || (effective === "dataset" && ds.length > MAX_CATEGORICAL);
  const colorByValue = effective !== "dataset" && effective !== "element";
  const fallback: ColorBy = tooMany(effective) && !multiples ? (mode === "atoms" ? "dataset" : "energy_per_atom") : effective;

  const nameOf = (id: string) => { const d = ds.find((x) => x.id === id) ?? byId.get(id); return d ? d.label || d.name : id; };
  const tip = (p: EmbPoint) => mode === "atoms"
    ? `${p.dataset} · ${nameOf(p.dataset)}\nframe ${p.frame}, atom ${p.atom} (${p.element})`
    : `${p.dataset} frame ${p.frame}\nE/atom ${p.energy_per_atom?.toFixed(4) ?? "–"} eV · max|F| ${p.fmax?.toFixed(3) ?? "–"} eV/Å\n${p.n_atoms} atoms`;

  const { points, range } = useMemo(() => {
    if (!q.data) return { points: [] as Point[], range: undefined as [number, number] | undefined };
    const order = q.data.datasets.map((d) => d.id);
    const val = (p: EmbPoint) => (colorByValue ? ((p as unknown as Record<string, number | null>)[fallback] ?? null) : null);
    const vals = q.data.points.map(val).filter((v): v is number => v !== null);
    const range: [number, number] | undefined = vals.length ? [Math.min(...vals), Math.max(...vals)] : undefined;
    const slot = (p: EmbPoint) => fallback === "dataset" ? order.indexOf(p.dataset) + 1
      : fallback === "element" ? shownElements.indexOf(p.element!) + 1 : null;
    const points = q.data.points.map((p) => ({
      x: p.pc1, y: p.pc2, key: `${p.dataset}/${p.frame}`, slot: slot(p), value: val(p), label: tip(p),
    }));
    return { points, range };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data, fallback, shownElements]);

  /** Draw order for the overlay plot: later points paint over earlier ones. */
  const drawn = useMemo(() => {
    const src = q.data?.points ?? [];
    const idx = src.map((_, i) => i);
    if (top === "mix") {
      let s = 12345; // seeded shuffle: stable across re-renders
      for (let i = idx.length - 1; i > 0; i--) {
        s = (Math.imul(s, 1103515245) + 12345) & 0x7fffffff;
        const j = s % (i + 1);
        [idx[i], idx[j]] = [idx[j], idx[i]];
      }
    } else if (top) {
      idx.sort((a, b) => Number(src[a].dataset === top) - Number(src[b].dataset === top) || a - b);
    }
    return idx.map((i) => points[i]);
  }, [q.data, points, top]);

  /** Small multiples: the panel's dataset in series-1 (or by element/value) on top of everyone else in grey. */
  const panel = (id: string) => {
    const mine = (q.data?.points ?? []).map((p, i) => [p, points[i]] as const);
    const rest = mine.filter(([p]) => p.dataset !== id).map(([, pt]) => ({ ...pt, slot: null, value: null }));
    const own = mine.filter(([p]) => p.dataset === id).map(([, pt]) => (fallback === "dataset" ? { ...pt, slot: 1 } : pt));
    return [...rest, ...own];
  };

  const candidates = (all.data?.items ?? []).filter((d) => {
    if (!filter) return true;
    const f = filter.toLowerCase();
    return [d.id, d.name, d.label ?? "", ...d.tags, ...d.chips.map((c) => c.text)].some((x) => x.toLowerCase().includes(f));
  });
  const togglePick = (id: string) => setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);
  const run = () => setMany({ ds: picked, el: elFilter.filter((e) => picked.some((i) => byId.get(i)?.elements.includes(e))) });
  const toggleEl = (e: string) => setMany({ el: elFilter.includes(e) ? elFilter.filter((x) => x !== e) : [...elFilter, e] });

  const legend = () => {
    if (multiples) return <p className="muted small">One panel per dataset: its {mode === "atoms" ? "atoms" : "frames"} {fallback === "dataset" ? <><Swatch v="--series-1" />highlighted</> : `coloured by ${fallback}`}, all other selected datasets in grey.</p>;
    if (fallback === "dataset")
      return <div className="legend-row">{ds.map((d, i) => <span key={d.id}><Swatch v={`--series-${i + 1}`} /><span className="mono">{d.id}</span> {d.label || d.name} ({q.data!.points.filter((p) => p.dataset === d.id).length})</span>)}</div>;
    if (fallback === "element")
      return <div className="legend-row">{shownElements.map((e, i) => <span key={e}><Swatch v={`--series-${i + 1}`} />{e} ({q.data!.points.filter((p) => p.element === e).length})</span>)}</div>;
    return (
      <div className="ptable-legend">
        {fallback}: {range ? range[0].toPrecision(4) : "–"}
        {SEQ.map((s) => <span key={s} className="sw" style={{ background: `var(${s})` }} />)}
        {range ? range[1].toPrecision(4) : "–"}
      </div>
    );
  };
  const pick = (k: string) => navigate(`/datasets/${k.replace("/", "/frames/")}`);

  return (
    <section>
      <div className="banner">EXPLORATORY / DIAGNOSTIC — descriptor projections are for spotting coverage and outliers, not quantitative results.</div>
      <div className="toggles" style={{ marginBottom: 8 }}>
        <span className="seg">
          <button className={mode === "frames" ? "on" : ""} onClick={() => setMode("frames")}>per frame</button>
          <button className={mode === "atoms" ? "on" : ""} onClick={() => setMode("atoms")} title="local atomic environments (per-atom MLIP features)">per atom</button>
        </span>
      </div>

      <details open={ids.length === 0} className="panel" style={{ marginBottom: 8 }}>
        <summary><strong>Datasets</strong> <span className="muted small">({picked.length} selected{mode === "frames" ? "; first = reference" : ""})</span></summary>
        <div className="picked">
          {picked.map((i) => <span key={i} className="badge" title="remove" onClick={() => togglePick(i)}>{i} {byId.get(i)?.label || byId.get(i)?.name || ""} ✕</span>)}
        </div>
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="filter: water, r2SCAN, cluster, PET-MAD, DS-2026-0000…" style={{ width: "100%", marginBottom: 4 }} />
        <div className="picker">
          {all.isLoading && <Loading />}
          {candidates.map((d) => (
            <label key={d.id} className={isLarge(d) ? "large" : ""} title={isLarge(d) ? "large dataset: skipped unless 'include large datasets' is ticked" : undefined}>
              <input type="checkbox" checked={picked.includes(d.id)} onChange={() => togglePick(d.id)} />
              <span className="mono small">{d.id}</span>
              <span><DsName name={d.name} label={d.label} chips={d.chips} />{isLarge(d) && <span className="badge warn">large</span>}</span>
            </label>
          ))}
        </div>
        <button className="primary" disabled={picked.length === 0} onClick={run}>Project {picked.length} dataset{picked.length === 1 ? "" : "s"}</button>
      </details>

      <div className="toggles" style={{ marginBottom: 8 }}>
        <label className="small">descriptor{" "}
          <select value={descriptor} onChange={(e) => setMany({ descriptor: e.target.value })}>
            {modeDescs.map((d) => (
              <option key={d.name} value={d.name} disabled={!d.available} title={d.description}>{d.name}{d.available ? "" : " (not installed)"}</option>
            ))}
          </select>
        </label>
        <label className="small">frames / dataset{" "}
          <select value={maxFrames} onChange={(e) => setMany({ max: e.target.value })}>
            {(mode === "atoms" ? [50, 200, 1000] : [100, 300, 1000, 3000]).map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        {mode === "atoms" && (
          <>
            <label className="small">atoms / dataset{" "}
              <select value={maxAtoms} onChange={(e) => setMany({ maxatoms: e.target.value })}>
                {[1000, 5000, 20000].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <span className="small">elements{" "}
              {elements.map((e) => (
                <label key={e} style={{ marginRight: 6 }}><input type="checkbox" checked={elFilter.includes(e)} onChange={() => toggleEl(e)} />{e}</label>
              ))}
              {elements.length > 0 && <span className="muted">{elFilter.length ? "" : "(all)"}</span>}
            </span>
            <label className="small"><input type="checkbox" checked={allowLarge} onChange={(e) => setMany({ large: e.target.checked ? "1" : null })} /> include large datasets</label>
          </>
        )}
        <label className="small">color by{" "}
          <select value={effective} onChange={(e) => setColorBy(e.target.value as ColorBy)}>
            {colorOptions.map((c) => <option key={c} value={c} disabled={c === "element" && tooMany(c)}>{c}{c === "element" && tooMany(c) ? ` (> ${MAX_CATEGORICAL}: filter elements)` : ""}</option>)}
          </select>
        </label>
        <label className="small" title="one panel per dataset"><input type="checkbox" checked={multiples} disabled={effective === "dataset" && ds.length > MAX_CATEGORICAL} onChange={(e) => setSplit(e.target.checked)} /> split by dataset</label>
        <label className="small" title="marker opacity: lower it to see overlapping datasets">opacity{" "}
          <input type="range" min={0.05} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} style={{ verticalAlign: "middle", width: 100 }} />
          <span className="mono"> {opacity.toFixed(2)}</span>
        </label>
        {!multiples && ds.length > 1 && (
          <label className="small" title="which points are drawn last (on top)">on top{" "}
            <select value={top} onChange={(e) => setTop(e.target.value)}>
              <option value="">last dataset (default)</option>
              <option value="mix">interleaved</option>
              {ds.map((d) => <option key={d.id} value={d.id}>{d.id} {d.label || d.name}</option>)}
            </select>
          </label>
        )}
      </div>

      {ids.length === 0 && <p className="muted">Pick datasets above. {mode === "atoms"
        ? "Points are atoms (local environments) from randomly sampled frames, balanced per element; restrict to one element (e.g. O) to compare like with like."
        : "Points are frames, sampled uniformly at random (seeded)."}</p>}
      {q.isFetching && <Loading what={mode === "atoms" ? "MLIP features (first run loads the model)" : "descriptors"} />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && (
        <>
          <p className="muted small">{q.data.note} Descriptor {q.data.descriptor}, {q.data.n_features} features; PC1 {(q.data.explained_variance[0] * 100).toFixed(1)} %, PC2 {(q.data.explained_variance[1] * 100).toFixed(1)} % of variance.{q.data.cache && ` Descriptors: ${q.data.cache.frames_cached} frames cached, ${q.data.cache.frames_computed} computed.`}</p>
          {(q.data.skipped ?? []).length > 0 && (
            <p className="small"><span className="badge warn">skipped</span> {q.data.skipped!.map((s) => `${s.id} (${s.reason})`).join("; ")}</p>
          )}
          {legend()}
          {multiples ? (
            <div className="multiples">
              {ds.map((d) => (
                <div key={d.id}>
                  <div className="small" style={{ margin: "4px 0" }}><span className="mono">{d.id}</span> <strong>{d.label || d.name}</strong></div>
                  <Scatter points={panel(d.id)} xLabel="PC1" yLabel="PC2" valueRange={range} onPick={pick} height={300} opacity={opacity} />
                </div>
              ))}
            </div>
          ) : (
            <Scatter points={drawn} xLabel="PC1" yLabel="PC2" valueRange={range} onPick={pick} opacity={opacity} />
          )}
          {mode === "atoms" && (
            <table className="grid small" style={{ marginTop: 8 }}>
              <thead><tr><th>Dataset</th><th className="num">atoms</th>{shownElements.map((e) => <th key={e} className="num">{e}</th>)}</tr></thead>
              <tbody>{ds.map((d) => (
                <tr key={d.id}><td><NodeLink id={d.id} /> {d.label || d.name}</td><td className="num">{d.n_atoms}</td>
                  {shownElements.map((e) => <td key={e} className="num">{d.elements?.[e] ?? 0}</td>)}</tr>
              ))}</tbody>
            </table>
          )}
        </>
      )}
      {sim.data && (
        <div className="panel" style={{ marginTop: 12 }}>
          <h2>Is <NodeLink id={sim.data.other} /> different from <NodeLink id={sim.data.reference} />?</h2>
          <p><strong>{sim.data.verdict}</strong> — {(sim.data.novel_fraction * 100).toFixed(1)} % of {sim.data.n_other_frames} frames are farther from every reference frame than 95 % of reference frames are from each other.</p>
          {(sim.data.composition.elements_only_in_other.length > 0 || sim.data.composition.elements_only_in_reference.length > 0) && (
            <p>Elements only in other: {sim.data.composition.elements_only_in_other.join(" ") || "–"} · only in reference: {sim.data.composition.elements_only_in_reference.join(" ") || "–"}</p>
          )}
          <p className="small">
            {Object.entries(sim.data.distributions).filter(([k]) => ["energy_per_atom", "force_norm", "n_atoms", "density"].includes(k)).map(([k, v]) => (
              <span key={k} style={{ marginRight: 16 }}>{k}: {v.mean_shift_std >= 0 ? "+" : ""}{v.mean_shift_std.toFixed(2)} σ, overlap {v.hist_overlap?.toFixed(2) ?? "–"}</span>
            ))}
          </p>
          <p className="small">most novel: {sim.data.most_novel_frames.slice(0, 8).map((m) => (
            <a key={m.frame} href={`/datasets/${m.dataset}/frames/${m.frame}`} style={{ marginRight: 8 }}>#{m.frame}</a>
          ))}</p>
        </div>
      )}
    </section>
  );
}
