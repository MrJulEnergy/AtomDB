import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Completeness, DatasetDetail, FrameRow, Page, PropertyDef, get } from "../api";
import { ErrorBox, KV, Labels, Loading, NodeLink, Pager, Panel, ProvButtons } from "../components/common";
import ProvSummary from "../components/ProvSummary";
import { fmtBytes, fmtNum, fmtRange } from "../format";

export default function DatasetPage() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["dataset", id], queryFn: () => get<DatasetDetail>(`/datasets/${id}`) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  return (
    <section>
      <div className="page-head">
        <h1>
          <span className="mono">{d.id}</span> {d.name}
          {d.status !== "active" && <span className="badge warn">{d.status}</span>}
        </h1>
        <ProvButtons id={d.id} />
      </div>
      {d.description && <p>{d.description}</p>}
      <div className="panels">
        <Panel title="Summary">
          <KV
            rows={[
              ["Frames", d.n_frames?.toLocaleString()],
              ["Atoms / frame", fmtRange(d.n_atoms_min, d.n_atoms_max, 0)],
              ["Composition", d.formula_summary],
              ["Elements", d.elements.join(" ")],
              ["Labels", <Labels e={d.has_energy} f={d.has_forces} s={d.has_stress} />],
              ["Energy range", d.energy_min !== null ? `${fmtRange(d.energy_min, d.energy_max, 4)} eV` : null],
              ["max |F|", d.fmax_max !== null ? `${fmtNum(d.fmax_max)} eV/Å` : null],
              ["Tags", d.tags.join(", ")],
              ["Created", d.created_at.replace("T", " ").slice(0, 19)],
              ["Source", d.source_text],
            ]}
          />
          <p><Link to={`/stats?ds=${d.id}`}>statistics &amp; histograms →</Link></p>
        </Panel>
        <Panel title="How was this made?" wide>
          <ProvSummary id={d.id} />
        </Panel>
        <CompletenessPanel id={d.id} />
        <Panel title="Files (storage)" wide>
          {groupFiles(d.files).map(({ file: f, roles }) => (
            <div key={f.id} className="file">
              <div>
                {roles.map((r) => <span key={r} className="badge">{r}</span>)} <NodeLink id={f.id} /> {f.name} · {fmtBytes(f.size_bytes)} · {f.format ?? "?"}
              </div>
              <div className="mono small">sha256 {f.sha256}</div>
              <div className="mono small">store {f.store_path}</div>
              {f.origins.map((o) => (
                <div key={o.original_path + o.hostname} className="mono small muted">
                  from {o.hostname}:{o.original_path}
                </div>
              ))}
            </div>
          ))}
        </Panel>
        {d.notes.length > 0 && (
          <Panel title="Notes">
            {d.notes.map((n, i) => <p key={i}>{n}</p>)}
          </Panel>
        )}
        <PropertiesPanel id={d.id} />
        <FramesPanel id={d.id} />
      </div>
    </section>
  );
}

/** One entry per stored file, listing all its roles (an H5MD import is original and canonical). */
function groupFiles(files: DatasetDetail["files"]) {
  const out: { file: DatasetDetail["files"][number]["file"]; roles: string[] }[] = [];
  for (const f of files) {
    const hit = out.find((x) => x.file.id === f.file.id);
    if (hit) hit.roles.push(f.role);
    else out.push({ file: f.file, roles: [f.role] });
  }
  return out;
}

function CompletenessPanel({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["completeness", id], queryFn: () => get<Completeness>(`/datasets/${id}/completeness`) });
  if (!q.data) return null;
  const c = q.data;
  return (
    <Panel title={`Provenance completeness ${Math.round(c.score * 100)}%`}>
      <ul className="checklist">
        {c.checks.map((x) => (
          <li key={x.item} className={x.ok ? "ok" : "missing"}>
            {x.ok ? "✓" : "✗"} {x.item}
            {x.hint && <div className="small muted">{x.hint}</div>}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function PropertiesPanel({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["props", id], queryFn: () => get<PropertyDef[]>(`/datasets/${id}/properties`) });
  if (!q.data) return null;
  return (
    <Panel title="Stored properties" wide>
      <table className="grid compact">
        <thead>
          <tr><th>Origin</th><th>Name</th><th>Level</th><th>dtype</th><th>Shape</th><th>Unit</th><th className="num">Frames</th><th>Computed by</th></tr>
        </thead>
        <tbody>
          {q.data.map((p) => (
            <tr key={p.origin + p.name}>
              <td>{p.origin}</td>
              <td className="mono">{p.name}</td>
              <td>{p.level}</td>
              <td>{p.dtype}</td>
              <td className="mono">{p.shape ? `(${p.shape.map((x) => (x === -1 ? "N" : x)).join(", ")})` : ""}</td>
              <td>{p.unit ?? ""}</td>
              <td className="num">{p.count}</td>
              <td>{p.label_calc_id && <NodeLink id={p.label_calc_id} />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function FramesPanel({ id }: { id: string }) {
  const [offset, setOffset] = useState(0);
  const limit = 50;
  const q = useQuery({
    queryKey: ["frames", id, offset],
    queryFn: () => get<Page<FrameRow>>(`/datasets/${id}/frames`, { offset, limit }),
    placeholderData: (p) => p,
  });
  if (!q.data) return null;
  return (
    <Panel title="Frames" wide>
      <table className="grid compact">
        <thead>
          <tr><th className="num">#</th><th className="num">Atoms</th><th>Formula</th><th className="num">E [eV]</th><th className="num">E/atom</th><th className="num">max |F|</th><th className="num">T [K]</th><th className="num">V [Å³]</th><th className="num">time [fs]</th></tr>
        </thead>
        <tbody>
          {q.data.items.map((f) => (
            <tr key={f.idx}>
              <td className="num"><Link to={`/datasets/${id}/frames/${f.idx}`}>{f.idx}</Link></td>
              <td className="num">{f.n_atoms}</td>
              <td className="mono">{f.formula}</td>
              <td className="num">{fmtNum(f.energy, 4)}</td>
              <td className="num">{fmtNum(f.energy_per_atom, 4)}</td>
              <td className="num">{fmtNum(f.fmax)}</td>
              <td className="num">{fmtNum(f.temperature, 1)}</td>
              <td className="num">{fmtNum(f.volume, 1)}</td>
              <td className="num">{fmtNum(f.time, 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Pager total={q.data.total} offset={offset} limit={limit} onChange={setOffset} />
    </Panel>
  );
}
