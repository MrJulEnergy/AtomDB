import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Graph, NodeDesc, get, nodeHref } from "../api";
import { ErrorBox, KV, Loading, NodeLink } from "../components/common";
import ProvGraph, { KIND_COLORS, LEGEND } from "../components/ProvGraph";

export default function GraphPage() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const dir = (params.get("dir") ?? "up") as "up" | "down" | "both";
  const [hideFiles, setHideFiles] = useState(true);
  const [selected, setSelected] = useState<string | null>(id ?? null);
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["prov", id, dir], queryFn: () => get<Graph>(`/nodes/${id}/provenance`, { direction: dir }) });

  return (
    <section className="graph-page">
      <div className="page-head">
        <h1>
          {dir === "up" ? "Where did this come from?" : dir === "down" ? "What was this used for?" : "Provenance"} <NodeLink id={id!} />
        </h1>
        <div className="toggles">
          {(["up", "down", "both"] as const).map((d) => (
            <button key={d} className={d === dir ? "btn primary" : "btn"} onClick={() => setParams({ dir: d })}>
              {d === "up" ? "↑ origin" : d === "down" ? "↓ uses" : "↕ both"}
            </button>
          ))}
          <label className="small"><input type="checkbox" checked={hideFiles} onChange={(e) => setHideFiles(e.target.checked)} /> hide files &amp; import steps</label>
        </div>
      </div>
      <div className="legend small">
        {LEGEND.map(([k, label]) => (
          <span key={k}><i style={{ borderColor: KIND_COLORS[k] }} />{label}</span>
        ))}
        <span className="muted">click: details · double-click: re-center · dashed: placeholder</span>
      </div>
      {q.isLoading && <Loading what="graph" />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && (
        <div className="graph-layout">
          <ProvGraph
            graph={q.data}
            hideFiles={hideFiles}
            selected={selected}
            onSelect={setSelected}
            onReroot={(n) => navigate(`/provenance/${n}?dir=${dir}`)}
          />
          <aside className="side">{selected ? <NodeSide id={selected} /> : <p className="muted">click a node</p>}</aside>
        </div>
      )}
      {q.data && q.data.nodes.length === 1 && (
        <p className="muted">No {dir === "down" ? "uses" : "provenance"} recorded yet.</p>
      )}
    </section>
  );
}

function NodeSide({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["node", id], queryFn: () => get<NodeDesc>(`/nodes/${id}`) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const n = q.data!;
  const blocks = ["dft", "md", "sampling", "training"].filter((b) => n[b]);
  return (
    <div>
      <h2 className="mono">{n.id}</h2>
      <p><strong>{n.name}</strong>{n.status !== "active" && <span className="badge warn">{n.status}</span>}</p>
      <p className="muted small">{n.subtitle}</p>
      <p><Link to={nodeHref(n.id)}>open full page →</Link></p>
      {n.kind === "dataset" && (
        <KV rows={[["frames", n.n_frames], ["elements", (n.elements ?? []).join(" ")], ["formula", n.formula_summary]]} />
      )}
      {blocks.map((b) => (
        <div key={b}>
          <h3>{b.toUpperCase()}</h3>
          <KV rows={Object.entries(n[b]).map(([k, v]) => [k, String(v)] as [string, string])} />
        </div>
      ))}
      {n.kind === "hpc_job" && (
        <KV rows={[["cluster", n.cluster], ["partition", n.partition], ["job", n.job_id], ["cpus", n.n_cpus], ["gpus", n.n_gpus], ["state", n.state], ["workdir", n.workdir]]} />
      )}
      {n.kind === "model" && (
        <KV rows={[["family", n.family], ["version", n.version], ["cutoff", n.cutoff], ["package", n.package], ["git", n.git_commit], ["url", n.url]]} />
      )}
      {n.kind === "file" && <KV rows={[["sha256", <span className="mono small">{n.sha256}</span>], ["format", n.format], ["store", <span className="mono small">{n.store_path}</span>]]} />}
      {n.workdir && <KV rows={[["workdir", <span className="mono small">{n.workdir}</span>]]} />}
    </div>
  );
}
