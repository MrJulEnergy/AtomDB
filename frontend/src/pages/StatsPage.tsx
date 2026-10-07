import { useQuery } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { get } from "../api";
import { ErrorBox, Loading, NodeLink, ProvButtons } from "../components/common";
import Histogram from "../components/Histogram";
import { DatasetStats, GROUPS, LABELS, Quantity, num } from "../stats";

export default function StatsPage() {
  const [params, setParams] = useSearchParams();
  const ds = params.get("ds") ?? "";
  const [draft, setDraft] = useState(ds);
  const [logY, setLogY] = useState(false);
  const q = useQuery({ queryKey: ["stats", ds], queryFn: () => get<DatasetStats>(`/datasets/${ds}/stats`), enabled: !!ds });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setParams({ ds: draft.trim() });
  };
  return (
    <section>
      <form className="searchbar" onSubmit={submit}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="dataset ID, e.g. DS-2026-000003" />
        <button type="submit">Show statistics</button>
        <label className="small"><input type="checkbox" checked={logY} onChange={(e) => setLogY(e.target.checked)} /> log counts</label>
      </form>
      {!ds && <p className="muted">Enter a dataset ID (or open statistics from a dataset page).</p>}
      {q.isLoading && <Loading what="statistics (streaming the file)" />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && <StatsView st={q.data} logY={logY} />}
    </section>
  );
}

function Card({ k, q, logY }: { k: string; q: Quantity; logY: boolean }) {
  if (!q.count || !q.hist) return null;
  if (q.std === 0 || q.min === q.max) {
    // a distribution of one value is not a chart
    return (
      <div className="chart-card">
        <p className="title">{LABELS[k] ?? k}</p>
        <p className="hero">{num(q.min, 6)} <span className="muted small">{q.unit}</span></p>
        <div className="stats-line">constant over all {q.count.toLocaleString()} values</div>
      </div>
    );
  }
  return (
    <div className="chart-card">
      <Histogram title={`${LABELS[k] ?? k}${q.unit ? ` [${q.unit}]` : ""}`} xLabel={q.unit || k} logY={logY}
        series={[{ label: LABELS[k] ?? k, slot: 1, edges: q.hist.edges, counts: q.hist.counts }]} />
      <div className="stats-line">
        n {q.count.toLocaleString()} · min {num(q.min)} · max {num(q.max)} · mean {num(q.mean)} · std {num(q.std)}
      </div>
    </div>
  );
}

function StatsView({ st, logY }: { st: DatasetStats; logY: boolean }) {
  const comp = Object.entries(st.composition);
  return (
    <>
      <div className="page-head">
        <h1><NodeLink id={st.dataset} /> {st.name} <span className="muted">· {st.n_frames} frames</span></h1>
        <ProvButtons id={st.dataset} />
      </div>
      {GROUPS.map(([title, keys]) => {
        const present = keys.filter((k) => st.quantities[k]?.count);
        if (!present.length) return null;
        return (
          <div key={title}>
            <h2>{title}</h2>
            <div className="charts-grid">
              {present.map((k) => <Card key={k} k={k} q={st.quantities[k]} logY={logY} />)}
            </div>
          </div>
        );
      })}
      {Object.keys(st.force_per_element).length > 0 && (
        <>
          <h2>|F| per element — outliers here often flag bad DFT frames</h2>
          <div className="charts-grid">
            {Object.entries(st.force_per_element).map(([el, q]) => <Card key={el} k={`|F| ${el}`} q={{ ...q, unit: "eV/Å" }} logY={logY} />)}
          </div>
        </>
      )}
      <h2>Composition</h2>
      <table className="grid compact" style={{ maxWidth: 480 }}>
        <thead><tr><th>Formula</th><th className="num">Frames</th></tr></thead>
        <tbody>
          {comp.slice(0, 50).map(([f, n]) => <tr key={f}><td className="mono">{f}</td><td className="num">{n}</td></tr>)}
        </tbody>
      </table>
      {comp.length > 50 && <p className="muted small">… {comp.length - 50} more compositions</p>}
    </>
  );
}
