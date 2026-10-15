import { useQuery } from "@tanstack/react-query";
import { Graph, get } from "../api";
import { ErrorBox, Loading, NodeLink } from "./common";

const GROUPS: [string, (n: Graph["nodes"][number]) => boolean][] = [
  ["DFT / labels", (n) => n.calc_type === "dft"],
  ["MD simulations", (n) => n.calc_type === "md"],
  ["Models (MLIP)", (n) => n.kind === "model"],
  ["Sampling", (n) => n.calc_type === "sampling"],
  ["Training", (n) => n.calc_type === "training"],
  ["Source datasets", (n) => n.kind === "dataset"],
  ["HPC jobs", (n) => n.kind === "hpc_job"],
  ["Other calculations", (n) => n.kind === "calculation" && !["dft", "md", "sampling", "training", "import", "conversion"].includes(n.calc_type ?? "")],
];

/** Compact "what produced this" summary built from the upstream graph. */
export default function ProvSummary({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["prov", id, "up"], queryFn: () => get<Graph>(`/nodes/${id}/provenance`, { direction: "up" }) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const nodes = q.data!.nodes.filter((n) => n.id !== id);
  const groups = GROUPS.map(([title, pred]) => [title, nodes.filter(pred)] as const).filter(([, ns]) => ns.length);
  if (!groups.length) return <p className="muted">No provenance beyond the import. Use <b>✎ edit metadata</b> above (or <code>atomdb annotate</code>).</p>;
  return (
    <div className="prov-summary">
      {groups.map(([title, ns]) => (
        <div key={title}>
          <h3>{title}</h3>
          <ul>
            {ns.slice(0, 12).map((n) => (
              <li key={n.id}>
                <NodeLink id={n.id} /> <span title={n.label ? n.name : undefined}>{n.label || n.name}</span>
                {n.status !== "active" && <span className="badge warn">{n.status}</span>}
                {n.subtitle && <span className="muted"> — {n.subtitle}</span>}
              </li>
            ))}
            {ns.length > 12 && <li className="muted">… {ns.length - 12} more</li>}
          </ul>
        </div>
      ))}
    </div>
  );
}
