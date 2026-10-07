import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { DatasetSummary, Page, get } from "../api";
import { fmtDate, fmtRange } from "../format";

export default function DatasetsPage() {
  const q = useQuery({
    queryKey: ["datasets"],
    queryFn: () => get<Page<DatasetSummary>>("/datasets", { limit: 200 }),
  });
  if (q.isLoading) return <p className="muted">loading…</p>;
  if (q.error) return <p className="error">{String(q.error)}</p>;
  const page = q.data!;
  return (
    <section>
      <h1>Datasets <span className="muted">({page.total})</span></h1>
      <table className="grid">
        <thead>
          <tr>
            <th>ID</th><th>Name</th><th className="num">Frames</th><th className="num">Atoms</th>
            <th>Elements</th><th>Labels</th><th>Created</th>
          </tr>
        </thead>
        <tbody>
          {page.items.map((d) => (
            <tr key={d.id}>
              <td className="mono"><Link to={`/datasets/${d.id}`}>{d.id}</Link></td>
              <td>{d.name}</td>
              <td className="num">{d.n_frames ?? "–"}</td>
              <td className="num">{fmtRange(d.n_atoms_min, d.n_atoms_max, 0)}</td>
              <td>{d.elements.join(" ")}</td>
              <td>{[d.has_energy && "E", d.has_forces && "F", d.has_stress && "S"].filter(Boolean).join(" ")}</td>
              <td>{fmtDate(d.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
