import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { NodeBrief, Page, get } from "../api";
import { ErrorBox, Loading, NodeLink, Pager } from "../components/common";

const TITLES: Record<string, string> = { calculations: "Calculations", models: "Models", jobs: "HPC jobs", files: "Files" };

export default function NodesPage() {
  const { kind = "calculations" } = useParams();
  const [offset, setOffset] = useState(0);
  const limit = 100;
  const q = useQuery({
    queryKey: ["nodes", kind, offset],
    queryFn: () => get<Page<NodeBrief>>("/nodes", { kind, offset, limit }),
  });
  return (
    <section>
      <h1>{TITLES[kind] ?? kind} {q.data && <span className="muted">({q.data.total})</span>}</h1>
      {q.isLoading && <Loading />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && (
        <>
          <table className="grid">
            <thead><tr><th>ID</th><th>Name</th><th>Type</th><th>Status</th><th>Details</th></tr></thead>
            <tbody>
              {q.data.items.map((n) => (
                <tr key={n.id} className={n.status !== "active" ? "dim-row" : ""}>
                  <td><NodeLink id={n.id} /></td>
                  <td className="wrap">{n.name}</td>
                  <td>{n.calc_type ?? n.kind}</td>
                  <td>{n.status}</td>
                  <td className="wrap muted">{n.subtitle}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager total={q.data.total} offset={offset} limit={limit} onChange={setOffset} />
        </>
      )}
    </section>
  );
}
