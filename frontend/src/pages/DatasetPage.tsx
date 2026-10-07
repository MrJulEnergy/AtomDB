import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { DatasetDetail, get } from "../api";
import { fmtBytes, fmtRange } from "../format";

export default function DatasetPage() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["dataset", id], queryFn: () => get<DatasetDetail>(`/datasets/${id}`) });
  if (q.isLoading) return <p className="muted">loading…</p>;
  if (q.error) return <p className="error">{String(q.error)}</p>;
  const d = q.data!;
  return (
    <section>
      <h1><span className="mono">{d.id}</span> {d.name}</h1>
      {d.description && <p>{d.description}</p>}
      <div className="panels">
        <div className="panel">
          <h2>Summary</h2>
          <dl>
            <dt>Frames</dt><dd>{d.n_frames ?? "–"}</dd>
            <dt>Atoms / frame</dt><dd>{fmtRange(d.n_atoms_min, d.n_atoms_max, 0)}</dd>
            <dt>Formula</dt><dd>{d.formula_summary ?? "–"}</dd>
            <dt>Elements</dt><dd>{d.elements.join(" ") || "–"}</dd>
            <dt>Tags</dt><dd>{d.tags.join(", ") || "–"}</dd>
            <dt>Created</dt><dd>{d.created_at}</dd>
          </dl>
        </div>
        <div className="panel wide">
          <h2>Files</h2>
          {d.files.map((f) => (
            <div key={f.file.id + f.role} className="file">
              <div><span className="badge">{f.role}</span> <span className="mono">{f.file.id}</span> {f.file.name} · {fmtBytes(f.file.size_bytes)} · {f.file.format ?? "?"}</div>
              <div className="mono small">sha256 {f.file.sha256}</div>
              <div className="mono small">store {f.file.store_path}</div>
              {f.file.origins.map((o) => (
                <div key={o.original_path} className="mono small muted">from {o.hostname}:{o.original_path}</div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
