import { useQuery } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { SearchResult, get } from "../api";
import { ErrorBox, Labels, Loading, NodeLink, Pager } from "../components/common";
import { fmtDate, fmtRange } from "../format";

const EXAMPLES = ["r2scan+d4", "el:K,Cl", "T:400", "model:MACE-MH-1", "es:any", "has:stress", "only:K,Cl,O,H"];
const LIMIT = 100;

export default function DatasetsPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const offset = Number(params.get("offset") ?? 0);
  const sort = params.get("sort") ?? "-created";
  const [draft, setDraft] = useState(q);

  const res = useQuery({
    queryKey: ["search", q, offset, sort],
    queryFn: () => get<SearchResult>("/search", { q, offset, limit: LIMIT, sort }),
    placeholderData: (prev) => prev,
  });

  const go = (next: Record<string, string>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) v ? p.set(k, v) : p.delete(k);
    setParams(p);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    go({ q: draft, offset: "" });
  };
  const sortBy = (key: string) => go({ sort: sort === key ? `-${key}`.replace("--", "") : key, offset: "" });

  return (
    <section>
      <form className="searchbar" onSubmit={submit}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="search: r2scan+d4  el:K,Cl  T:400  model:MACE-MH-1  from:MD-2026-000017 …"
          spellCheck={false}
          autoFocus
        />
        <button type="submit">Search</button>
      </form>
      <div className="examples">
        {EXAMPLES.map((x) => (
          <button key={x} className="chip" onClick={() => { setDraft(x); go({ q: x, offset: "" }); }}>{x}</button>
        ))}
        <Link className="chip" to="/elements">periodic table…</Link>
      </div>
      {res.isLoading && <Loading />}
      {res.error && <ErrorBox error={res.error} />}
      {res.data && (
        <>
          <p className="muted small">
            {res.data.total} dataset{res.data.total === 1 ? "" : "s"}
            {res.data.interpreted.length > 0 && <> · query: {res.data.interpreted.join("; ")}</>}
          </p>
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th className="sortable" onClick={() => sortBy("id")}>ID</th>
                  <th className="sortable" onClick={() => sortBy("name")}>Name</th>
                  <th className="num sortable" onClick={() => sortBy("frames")}>Frames</th>
                  <th className="num">Atoms</th>
                  <th>Elements</th>
                  <th>Labels</th>
                  <th className="num">Energy [eV]</th>
                  <th className="num">max |F|</th>
                  <th>Functional</th>
                  <th>Disp.</th>
                  <th>Code</th>
                  <th>MLIP</th>
                  <th className="num">T [K]</th>
                  <th className="num">p [bar]</th>
                  <th>Source</th>
                  <th className="sortable" onClick={() => sortBy("created")}>Created</th>
                </tr>
              </thead>
              <tbody>
                {res.data.items.map((d) => (
                  <tr key={d.id} className={d.status !== "active" ? "dim-row" : ""}>
                    <td><NodeLink id={d.id} /></td>
                    <td className="wrap name">{d.name}</td>
                    <td className="num">{d.n_frames?.toLocaleString() ?? "–"}</td>
                    <td className="num">{fmtRange(d.n_atoms_min, d.n_atoms_max, 0)}</td>
                    <td>{d.elements.join(" ")}</td>
                    <td><Labels e={d.has_energy} f={d.has_forces} s={d.has_stress} /></td>
                    <td className="num">{fmtRange(d.energy_min, d.energy_max, 2)}</td>
                    <td className="num">{d.fmax_max?.toFixed(2) ?? "–"}</td>
                    <td>{(d.extra.functional ?? []).join(", ")}</td>
                    <td>{(d.extra.dispersion ?? []).filter((x) => x !== "none").join(", ")}</td>
                    <td>{(d.extra.code ?? []).join(", ")}</td>
                    <td>{(d.extra.model ?? []).join(", ")}</td>
                    <td className="num">{(d.extra.temperature ?? []).join(", ")}</td>
                    <td className="num">{(d.extra.pressure ?? []).join(", ")}</td>
                    <td className="ids">{(d.extra.source_dataset ?? []).slice(0, 3).map((s) => <NodeLink key={s} id={s} />)}</td>
                    <td>{fmtDate(d.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager total={res.data.total} offset={offset} limit={LIMIT} onChange={(o) => go({ offset: String(o) })} />
        </>
      )}
    </section>
  );
}
