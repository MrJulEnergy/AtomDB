import { useQuery } from "@tanstack/react-query";
import { FormEvent, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { get } from "../api";
import { ErrorBox, Loading, NodeLink } from "../components/common";
import Scatter, { Point } from "../components/Scatter";

interface EmbPoint { dataset: string; frame: number; pc1: number; pc2: number; energy_per_atom: number | null; fmax: number | null; n_atoms: number | null; temperature: number | null }
interface Embedding {
  note: string;
  descriptor: string;
  n_features: number;
  explained_variance: number[];
  datasets: { id: string; name: string; n_frames: number }[];
  points: EmbPoint[];
}
interface Similarity {
  reference: string; other: string; novel_fraction: number; n_other_frames: number; novelty_threshold: number; verdict: string;
  composition: { elements_only_in_other: string[]; elements_only_in_reference: string[] };
  distributions: Record<string, { mean_shift_std: number; hist_overlap: number | null }>;
  most_novel_frames: { dataset: string; frame: number; nn_distance: number }[];
}

const COLOR_BY = ["dataset", "energy_per_atom", "fmax", "n_atoms", "temperature"] as const;
type ColorBy = (typeof COLOR_BY)[number];
const MAX_CATEGORICAL = 3; // scatter = all-pairs comparison: only 3 palette slots validate

export default function EmbeddingPage() {
  const [params, setParams] = useSearchParams();
  const ids = params.getAll("ds");
  const descriptor = params.get("descriptor") ?? "composition-rdf";
  const maxFrames = Number(params.get("max") ?? 300);
  const [draft, setDraft] = useState(ids.join(", "));
  const [colorBy, setColorBy] = useState<ColorBy>("dataset");
  const navigate = useNavigate();
  const descs = useQuery({ queryKey: ["descriptors"], queryFn: () => get<{ name: string; available: boolean; description: string }[]>("/descriptors") });
  const q = useQuery({
    queryKey: ["embedding", ids, descriptor, maxFrames],
    queryFn: () => get<Embedding>("/embedding", { ds: ids, descriptor, max_frames: maxFrames }),
    enabled: ids.length > 0,
    staleTime: Infinity,
  });
  const sim = useQuery({
    queryKey: ["similarity", ids[0], ids[1], descriptor],
    queryFn: () => get<Similarity>("/similarity", { reference: ids[0], other: ids[1], descriptor }),
    enabled: ids.length === 2,
    staleTime: Infinity,
  });
  const effective: ColorBy = colorBy === "dataset" && ids.length > MAX_CATEGORICAL ? "energy_per_atom" : colorBy;

  const { points, range } = useMemo(() => {
    if (!q.data) return { points: [] as Point[], range: undefined };
    const order = q.data.datasets.map((d) => d.id);
    const vals = q.data.points.map((p) => (effective === "dataset" ? null : (p[effective] as number | null))).filter((v): v is number => v !== null);
    const range: [number, number] | undefined = vals.length ? [Math.min(...vals), Math.max(...vals)] : undefined;
    const points = q.data.points.map((p) => ({
      x: p.pc1, y: p.pc2, key: `${p.dataset}/${p.frame}`,
      slot: effective === "dataset" ? order.indexOf(p.dataset) + 1 : null,
      value: effective === "dataset" ? null : (p[effective] as number | null),
      label: `${p.dataset} frame ${p.frame}\nE/atom ${p.energy_per_atom?.toFixed(4) ?? "–"} eV · max|F| ${p.fmax?.toFixed(3) ?? "–"} eV/Å\n${p.n_atoms} atoms`,
    }));
    return { points, range };
  }, [q.data, effective]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const p = new URLSearchParams();
    draft.split(/[\s,]+/).filter(Boolean).forEach((x) => p.append("ds", x));
    p.set("descriptor", descriptor);
    p.set("max", String(maxFrames));
    setParams(p);
  };
  const setParam = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    p.set(k, v);
    setParams(p);
  };

  return (
    <section>
      <div className="banner">EXPLORATORY / DIAGNOSTIC — descriptor projections are for spotting coverage and outliers, not quantitative results.</div>
      <form className="searchbar" onSubmit={submit}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="dataset IDs (first = reference), e.g. DS-2026-000001, DS-2026-000004" />
        <button type="submit">Project</button>
      </form>
      <div className="toggles" style={{ marginBottom: 8 }}>
        <label className="small">descriptor{" "}
          <select value={descriptor} onChange={(e) => setParam("descriptor", e.target.value)}>
            {(descs.data ?? [{ name: "composition-rdf", available: true, description: "" }]).map((d) => (
              <option key={d.name} value={d.name} disabled={!d.available}>{d.name}{d.available ? "" : " (not installed)"}</option>
            ))}
          </select>
        </label>
        <label className="small">frames / dataset{" "}
          <select value={maxFrames} onChange={(e) => setParam("max", e.target.value)}>
            {[100, 300, 1000, 3000].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label className="small">color by{" "}
          <select value={effective} onChange={(e) => setColorBy(e.target.value as ColorBy)}>
            {COLOR_BY.map((c) => <option key={c} value={c} disabled={c === "dataset" && ids.length > MAX_CATEGORICAL}>{c}</option>)}
          </select>
        </label>
      </div>
      {ids.length === 0 && <p className="muted">Enter one or more dataset IDs. Points are frames, sampled uniformly at random (seeded).</p>}
      {q.isLoading && <Loading what="descriptors" />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && (
        <>
          <p className="muted small">{q.data.note} Descriptor {q.data.descriptor}, {q.data.n_features} features; PC1 {(q.data.explained_variance[0] * 100).toFixed(1)} %, PC2 {(q.data.explained_variance[1] * 100).toFixed(1)} % of variance.</p>
          {effective === "dataset" ? (
            <div className="legend-row">
              {q.data.datasets.map((d, i) => (
                <span key={d.id}><i style={{ background: `var(--series-${i + 1})`, height: 8, width: 8, borderRadius: 4 }} />{d.id} {d.name} ({q.data.points.filter((p) => p.dataset === d.id).length})</span>
              ))}
            </div>
          ) : (
            <div className="ptable-legend">
              {effective}: {range ? range[0].toPrecision(4) : "–"}
              {["--seq-100", "--seq-200", "--seq-300", "--seq-400", "--seq-500", "--seq-600"].map((s) => <span key={s} className="sw" style={{ background: `var(${s})` }} />)}
              {range ? range[1].toPrecision(4) : "–"}
              {ids.length > MAX_CATEGORICAL && <span> · more than {MAX_CATEGORICAL} datasets: colored by value</span>}
            </div>
          )}
          <Scatter points={points} xLabel="PC1" yLabel="PC2" valueRange={range} onPick={(k) => navigate(`/datasets/${k.replace("/", "/frames/")}`)} />
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
