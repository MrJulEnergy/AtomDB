import { useQueries, useQuery } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { CategoryCounts, CategoryKey, DatasetSummary, SearchResult, get } from "../api";
import { ErrorBox, Labels, Loading, NodeLink, Pager } from "../components/common";
import DsName from "../components/DsName";
import { CopyForm } from "../components/EditMetadata";
import ShareCommand from "../components/ShareCommand";
import { fmtDate, fmtRange } from "../format";

const EXAMPLES = ["r2scan+d4", "el:K,Cl", "T:400", "model:MACE-MH-1", "es:any", "has:stress", "only:K,Cl,O,H"];
const LIMIT = 100;
const ALL = 1000; // grouped view shows everything on one page
const N_COLS = 17;
const N_METHOD_COLS = 6; // functional … pressure: hidden in the compact view (the chips carry them)

const CATEGORIES: { key: CategoryKey; title: string; labels?: Record<string, string> }[] = [
  { key: "system", title: "System" },
  { key: "project", title: "Project" },
  { key: "quality", title: "Quality warnings" },
  { key: "geometry", title: "Geometry",
    labels: { pbc: "PBC", "no-pbc": "no PBC", "partial-pbc": "partial PBC (slab / wire)", mixed: "mixed", unknown: "unknown" } },
  { key: "cell_shape", title: "Box shape (PBC)" },
  { key: "cell", title: "Box", labels: { fixed: "fixed box", variable: "variable box (NPT / relax)" } },
  { key: "solvation", title: "Implicit solvent", labels: { none: "none" } },
  { key: "data_type", title: "Data type",
    labels: { dft: "DFT reference", "mlip-md": "MLIP MD / sampling", "classical-md": "Classical MD", other: "other" } },
  { key: "dft_code", title: "DFT code" },
  { key: "mlip_family", title: "MLIP" },
  { key: "labels", title: "Labels" },
  { key: "atom_data", title: "Per-atom data" },
  { key: "composition", title: "Composition", labels: { fixed: "fixed composition", variable: "variable composition" } },
  { key: "n_elements", title: "Elements" },
  { key: "size", title: "Atoms per frame", labels: { small: "< 100", medium: "100–1000", large: "> 1000" } },
];
const catLabel = (key: string, value: string) => CATEGORIES.find((c) => c.key === key)?.labels?.[value] ?? value;

/** Search-language terms for the ticked sidebar values, optionally leaving one dimension out. */
function filterTerms(filters: Record<string, string[]>, except?: string): string {
  return Object.entries(filters)
    .filter(([k, v]) => k !== except && v.length > 0)
    .map(([k, v]) => `${k}:"${v.join(",")}"`)
    .join(" ");
}

export default function DatasetsPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const offset = Number(params.get("offset") ?? 0);
  const sort = params.get("sort") ?? "-created";
  const group = (params.get("group") ?? "") as CategoryKey | "";
  const wide = params.get("wide") === "1";
  const navigate = useNavigate();
  const [draft, setDraft] = useState(q);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const filters = Object.fromEntries(
    CATEGORIES.map((c) => [c.key, (params.get(c.key) ?? "").split(",").filter(Boolean)])) as Record<CategoryKey, string[]>;
  const fullQ = [q, filterTerms(filters)].filter(Boolean).join(" ");
  const limit = group ? ALL : LIMIT;
  // selection survives paging/searching so many files can be collected for one copy
  const [selected, setSelected] = useState<string[]>([]);
  const toggle = (id: string) => setSelected(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);

  const res = useQuery({
    queryKey: ["search", fullQ, group ? 0 : offset, sort, limit],
    queryFn: () => get<SearchResult>("/search", { q: fullQ, offset: group ? 0 : offset, limit, sort }),
    placeholderData: (prev) => prev,
  });
  // counts per dimension honour the other dimensions' filters, so ticked boxes don't zero their siblings
  const counts = useQueries({
    queries: CATEGORIES.map((c) => {
      const cq = [q, filterTerms(filters, c.key)].filter(Boolean).join(" ");
      return {
        queryKey: ["categories", cq],
        queryFn: () => get<CategoryCounts>("/categories", { q: cq }),
        placeholderData: (prev: CategoryCounts | undefined) => prev,
      };
    }),
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
  const toggleFilter = (key: CategoryKey, value: string) => {
    const cur = filters[key];
    go({ [key]: (cur.includes(value) ? cur.filter((x) => x !== value) : [...cur, value]).join(","), offset: "" });
  };
  const toggleGroup = (g: string) => setCollapsed(collapsed.includes(g) ? collapsed.filter((x) => x !== g) : [...collapsed, g]);
  const anyFilter = CATEGORIES.some((c) => filters[c.key].length > 0);
  const sortBy = (key: string) => go({ sort: sort === key ? `-${key}`.replace("--", "") : key, offset: "" });

  const row = (d: DatasetSummary, key?: string) => (
    <tr key={key ?? d.id} className={d.status !== "active" ? "dim-row" : ""}>
      <td><input type="checkbox" checked={selected.includes(d.id)} onChange={() => toggle(d.id)} /></td>
      <td><NodeLink id={d.id} /></td>
      <td className="wrap name"><DsName name={d.name} label={d.label} chips={d.chips} hide={["size"]} /></td>
      <td className="num">{d.n_frames?.toLocaleString() ?? "–"}</td>
      <td className="num">{fmtRange(d.n_atoms_min, d.n_atoms_max, 0)}</td>
      <td className="wrap" title={d.elements.join(" ")}>{d.elements.length > 8 ? `${d.elements.length} elements` : d.elements.join(" ")}</td>
      <td><Labels e={d.has_energy} f={d.has_forces} s={d.has_stress} /></td>
      <td className="num">{fmtRange(d.energy_min, d.energy_max, 2)}</td>
      <td className="num">{d.fmax_max?.toFixed(2) ?? "–"}</td>
      {wide && <>
        <td>{(d.extra.functional ?? []).join(", ")}</td>
        <td>{(d.extra.dispersion ?? []).filter((x) => x !== "none").join(", ")}</td>
        <td>{(d.extra.code ?? []).join(", ")}</td>
        <td>{(d.extra.model ?? []).join(", ")}</td>
        <td className="num">{(d.extra.temperature ?? []).join(", ")}</td>
        <td className="num">{(d.extra.pressure ?? []).join(", ")}</td>
      </>}
      <td className="ids">{(d.extra.source_dataset ?? []).slice(0, 3).map((s) => <NodeLink key={s} id={s} />)}</td>
      <td>{fmtDate(d.created_at)}</td>
    </tr>
  );
  const groupRows = (items: DatasetSummary[]) => {
    const order = (counts[CATEGORIES.findIndex((c) => c.key === group)]?.data?.[group as CategoryKey] ?? []).map((x) => x.value);
    const groups = new Map<string, DatasetSummary[]>();
    for (const d of items) {
      const vals = d.extra[group] ?? [];
      for (const v of vals.length ? vals : ["—"]) groups.set(v, [...(groups.get(v) ?? []), d]);
    }
    const keys = [...groups.keys()].sort((a, b) => {
      const ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia < 0 ? 1e9 : ia) - (ib < 0 ? 1e9 : ib) || a.localeCompare(b);
    });
    return keys.flatMap((g) => {
      const open = !collapsed.includes(g);
      return [
        <tr key={`group-${g}`} className="group-row" onClick={() => toggleGroup(g)}>
          <td colSpan={wide ? N_COLS : N_COLS - N_METHOD_COLS}>{open ? "▼" : "▶"} {catLabel(group, g)} <span className="muted">({groups.get(g)!.length})</span></td>
        </tr>,
        ...(open ? groups.get(g)!.map((d) => row(d, `${g}/${d.id}`)) : []),
      ];
    });
  };

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
      {selected.length > 0 && (
        <div className="selection-bar">
          <div className="panel-head">
            <strong>{selected.length} selected</strong>
            <button onClick={() => navigate(`/embedding?mode=atoms&${selected.map((s) => `ds=${s}`).join("&")}`)}
              title="PCA of per-atom MACE features of the selected datasets">compare atomic environments</button>
            <button onClick={() => setSelected([])}>clear selection</button>
          </div>
          <ShareCommand ids={selected} />
          <CopyForm key={selected.join()} targets={selected} />
        </div>
      )}
      <div className="datasets-layout">
        <aside className="cat-sidebar">
          {CATEGORIES.map((c, i) => {
            const vals = counts[i].data?.[c.key] ?? [];
            const shown = [...vals, ...filters[c.key].filter((v) => !vals.some((x) => x.value === v)).map((value) => ({ value, count: 0 }))];
            return (
              <div key={c.key} className="cat-block">
                <h3>{c.title}</h3>
                {shown.length === 0 && <div className="muted small">–</div>}
                {shown.map((x) => (
                  <label key={x.value} className="cat-option">
                    <input type="checkbox" checked={filters[c.key].includes(x.value)} onChange={() => toggleFilter(c.key, x.value)} />
                    <span>{catLabel(c.key, x.value)}</span>
                    <span className="muted">{x.count}</span>
                  </label>
                ))}
              </div>
            );
          })}
          {anyFilter && (
            <button className="small" onClick={() => go({ ...Object.fromEntries(CATEGORIES.map((c) => [c.key, ""])), offset: "" })}>clear filters</button>
          )}
        </aside>
        <div className="datasets-main">
          <div className="group-bar">
            <label>
              Group by{" "}
              <select value={group} onChange={(e) => { setCollapsed([]); go({ group: e.target.value, offset: "" }); }}>
                <option value="">none</option>
                {CATEGORIES.map((c) => <option key={c.key} value={c.key}>{c.title}</option>)}
              </select>
            </label>{" "}
            <label title="show functional, dispersion, code, MLIP, T and p as separate columns">
              <input type="checkbox" checked={wide} onChange={(e) => go({ wide: e.target.checked ? "1" : "" })} /> method columns
            </label>
          </div>
          {res.isLoading && <Loading />}
          {res.error && <ErrorBox error={res.error} />}
          {res.data && (
            <>
              <p className="muted small">
                {res.data.total} dataset{res.data.total === 1 ? "" : "s"}
                {res.data.interpreted.length > 0 && <> · query: {res.data.interpreted.join("; ")}</>}
                {group && res.data.total > ALL && <> · grouped view shows the first {ALL}</>}
              </p>
              <div className="table-wrap">
                <table className="grid">
                  <thead>
                    <tr>
                      <th>
                        <input type="checkbox" title="select all on this page"
                          checked={res.data.items.length > 0 && res.data.items.every((d) => selected.includes(d.id))}
                          onChange={(e) => {
                            const ids = res.data!.items.map((d) => d.id);
                            setSelected(e.target.checked ? [...new Set([...selected, ...ids])] : selected.filter((x) => !ids.includes(x)));
                          }} />
                      </th>
                      <th className="sortable" onClick={() => sortBy("id")}>ID</th>
                      <th className="sortable" onClick={() => sortBy("name")}>Name</th>
                      <th className="num sortable" onClick={() => sortBy("frames")}>Frames</th>
                      <th className="num">Atoms</th>
                      <th>Elements</th>
                      <th>Labels</th>
                      <th className="num">Energy [eV]</th>
                      <th className="num">max |F|</th>
                      {wide && <>
                        <th>Functional</th>
                        <th>Disp.</th>
                        <th>Code</th>
                        <th>MLIP</th>
                        <th className="num">T [K]</th>
                        <th className="num">p [bar]</th>
                      </>}
                      <th>Source</th>
                      <th className="sortable" onClick={() => sortBy("created")}>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {group ? groupRows(res.data.items) : res.data.items.map((d) => row(d))}
                  </tbody>
                </table>
              </div>
              {!group && <Pager total={res.data.total} offset={offset} limit={LIMIT} onChange={(o) => go({ offset: String(o) })} />}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
