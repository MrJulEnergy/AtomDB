import { useQuery } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { get } from "../api";
import { ErrorBox, Loading, NodeLink } from "../components/common";
import Histogram from "../components/Histogram";
import { DatasetStats, GROUPS, LABELS, num } from "../stats";

interface CompareResult {
  datasets: DatasetStats[];
  summary: Record<string, { reference: string; vs: { dataset: string; mean_shift_std: number; hist_overlap: number | null }[] }> & {
    elements?: Record<string, string[]>;
  };
}

const MAX = 8; // categorical palette has 8 fixed slots; never generate more hues

export default function ComparePage() {
  const [params, setParams] = useSearchParams();
  const ids = params.getAll("ds");
  const [draft, setDraft] = useState(ids.join(", "));
  const [normalize, setNormalize] = useState(true);
  const [logY, setLogY] = useState(false);
  const q = useQuery({
    queryKey: ["compare", ids],
    queryFn: () => get<CompareResult>("/compare", { ds: ids }),
    enabled: ids.length >= 2,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const next = new URLSearchParams();
    draft.split(/[\s,]+/).filter(Boolean).slice(0, MAX).forEach((x) => next.append("ds", x));
    setParams(next);
  };
  return (
    <section>
      <form className="searchbar" onSubmit={submit}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="reference dataset first: DS-2026-000003, DS-2026-000004 …" />
        <button type="submit">Compare</button>
        <label className="small"><input type="checkbox" checked={normalize} onChange={(e) => setNormalize(e.target.checked)} /> normalise</label>
        <label className="small"><input type="checkbox" checked={logY} onChange={(e) => setLogY(e.target.checked)} /> log</label>
      </form>
      {ids.length < 2 && <p className="muted">Enter two to {MAX} dataset IDs. The first one is the reference (e.g. the training set).</p>}
      {q.isLoading && <Loading what="comparison" />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && <CompareView r={q.data} normalize={normalize} logY={logY} />}
    </section>
  );
}

function CompareView({ r, normalize, logY }: { r: CompareResult; normalize: boolean; logY: boolean }) {
  const ds = r.datasets;
  const label = (d: DatasetStats) => `${d.dataset} ${d.name}`;
  const keys = GROUPS.flatMap(([, ks]) => ks).filter((k) => ds.some((d) => d.quantities[k]?.count));
  const elements = Array.from(new Set(ds.flatMap((d) => Object.keys(d.force_per_element))));
  return (
    <>
      <h2>Summary vs reference <NodeLink id={ds[0].dataset} /></h2>
      <table className="grid compact">
        <thead>
          <tr>
            <th>Quantity</th>
            {ds.map((d, i) => <th key={d.dataset} className="num"><i className="sw" style={{ background: `var(--series-${i + 1})`, display: "inline-block", width: 10, height: 3, marginRight: 4, verticalAlign: "middle" }} />{d.dataset} mean ± std</th>)}
            {ds.slice(1).map((d) => <th key={d.dataset} className="num">shift / overlap {d.dataset}</th>)}
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k}>
              <td>{LABELS[k] ?? k} <span className="muted">{ds.find((d) => d.quantities[k])?.quantities[k].unit}</span></td>
              {ds.map((d) => {
                const q = d.quantities[k];
                return <td key={d.dataset} className="num">{q?.count ? `${num(q.mean)} ± ${num(q.std, 3)}` : "–"}</td>;
              })}
              {ds.slice(1).map((d) => {
                const v = r.summary[k]?.vs.find((x) => x.dataset === d.dataset);
                return <td key={d.dataset} className="num">{v ? `${v.mean_shift_std >= 0 ? "+" : ""}${v.mean_shift_std.toFixed(2)} σ / ${v.hist_overlap === null ? "–" : v.hist_overlap.toFixed(2)}` : "–"}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">shift: difference of means in pooled standard deviations · overlap: shared area of normalised histograms (1 = identical, 0 = disjoint)</p>
      {GROUPS.map(([title, ks]) => {
        const present = ks.filter((k) => ds.some((d) => d.quantities[k]?.count));
        if (!present.length) return null;
        return (
          <div key={title}>
            <h2>{title}</h2>
            <div className="charts-grid">
              {present.map((k) => (
                <div className="chart-card" key={k}>
                  <Histogram title={`${LABELS[k] ?? k}`} xLabel={ds.find((d) => d.quantities[k])?.quantities[k].unit ?? ""} normalize={normalize} logY={logY}
                    series={ds.filter((d) => d.quantities[k]?.hist).map((d) => ({ label: label(d), slot: ds.indexOf(d) + 1, ...d.quantities[k].hist! }))} />
                </div>
              ))}
            </div>
          </div>
        );
      })}
      {elements.length > 0 && (
        <>
          <h2>|F| per element</h2>
          <div className="charts-grid">
            {elements.map((el) => (
              <div className="chart-card" key={el}>
                <Histogram title={`|F| ${el}`} xLabel="eV/Å" normalize={normalize} logY={logY}
                  series={ds.filter((d) => d.force_per_element[el]?.hist).map((d) => ({ label: label(d), slot: ds.indexOf(d) + 1, ...d.force_per_element[el].hist! }))} />
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}
