import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { NodeDesc, get } from "../api";
import { ErrorBox, KV, Loading, NodeLink, Panel, ProvButtons } from "../components/common";
import { fmtBytes } from "../format";

const SKIP = new Set(["id", "kind", "name", "status", "subtitle", "description", "created_at", "updated_at", "tags", "attrs", "notes",
  "md", "dft", "sampling", "training", "enhanced_sampling", "code_params", "parameters", "software_versions", "origins", "checkpoint",
  "sbatch_options", "hyperparameters", "calc_type"]);

function show(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export default function NodePage() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["node", id], queryFn: () => get<NodeDesc>(`/nodes/${id}`) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const n = q.data!;
  const basic = Object.entries(n).filter(([k, v]) => !SKIP.has(k) && v !== null && v !== "" && typeof v !== "object");
  return (
    <section>
      <div className="page-head">
        <h1>
          <span className="mono">{n.id}</span> {n.name}
          {n.status !== "active" && <span className="badge warn">{n.status}</span>}
        </h1>
        <ProvButtons id={n.id} />
      </div>
      <p className="muted">{n.kind}{n.calc_type ? ` · ${n.calc_type}` : ""}{n.subtitle ? ` · ${n.subtitle}` : ""}</p>
      {n.description && <p>{n.description}</p>}
      <div className="panels">
        <Panel title="Details">
          <KV rows={[...basic.map(([k, v]) => [k, k === "size_bytes" ? fmtBytes(v as number) : show(v)] as [string, string]),
            ["created", n.created_at?.slice(0, 19).replace("T", " ")], ["tags", (n.tags ?? []).join(", ")]]} />
        </Panel>
        {(["dft", "md", "sampling", "training"] as const).map((b) =>
          n[b] ? (
            <Panel key={b} title={{ dft: "DFT settings", md: "MD settings", sampling: "Sampling", training: "Training" }[b]}>
              <KV rows={Object.entries(n[b]).map(([k, v]) => [k, show(v)] as [string, string])} />
            </Panel>
          ) : null,
        )}
        {(n.enhanced_sampling ?? []).map((es: Record<string, unknown>, i: number) => (
          <Panel key={i} title="Enhanced sampling">
            <KV rows={[["method", show(es.method)], ["software", show(es.software)], ["input file", es.input_file_id ? <NodeLink id={String(es.input_file_id)} /> : null]]} />
            {Array.isArray(es.collective_variables) && (
              <>
                <h3>Collective variables</h3>
                <pre className="code">{(es.collective_variables as unknown[]).map((c) => JSON.stringify(c)).join("\n")}</pre>
              </>
            )}
            {es.bias ? (<><h3>Bias</h3><pre className="code">{JSON.stringify(es.bias, null, 2)}</pre></>) : null}
          </Panel>
        ))}
        {n.attrs && Object.keys(n.attrs).length > 0 && (
          <Panel title="Metadata">
            <KV rows={Object.entries(n.attrs as Record<string, { value: unknown; unit: string | null; source: string }>).map(
              ([k, v]) => [k, <>{show(v.value)} {v.unit} <span className="muted small">({v.source})</span></>] as [string, React.ReactNode])} />
          </Panel>
        )}
        {n.software_versions && (
          <Panel title="Software (recorded)">
            <KV rows={Object.entries(n.software_versions).map(([k, v]) => [k, show(v)] as [string, string])} />
          </Panel>
        )}
        {n.parameters && (
          <Panel title="Parameters / QC" wide>
            <pre className="code">{JSON.stringify(n.parameters, null, 2)}</pre>
          </Panel>
        )}
        {n.sbatch_options && (
          <Panel title="#SBATCH options">
            <KV rows={Object.entries(n.sbatch_options).map(([k, v]) => [k, show(v)] as [string, string])} />
          </Panel>
        )}
        {n.code_params && Object.keys(n.code_params).length > 0 && <CodeParams params={n.code_params} />}
        {n.origins && (
          <Panel title="Imported from" wide>
            {n.origins.map((o: { hostname: string; original_path: string; imported_at: string }) => (
              <div key={o.original_path + o.hostname} className="mono small">{o.hostname}:{o.original_path} <span className="muted">({o.imported_at.slice(0, 19)})</span></div>
            ))}
          </Panel>
        )}
        {(n.notes ?? []).length > 0 && (
          <Panel title="Notes">{n.notes.map((t: string, i: number) => <p key={i}>{t}</p>)}</Panel>
        )}
      </div>
    </section>
  );
}

function CodeParams({ params }: { params: Record<string, unknown> }) {
  const [filter, setFilter] = useState("");
  const rows = Object.entries(params).filter(([k]) => k.toLowerCase().includes(filter.toLowerCase())).sort(([a], [b]) => a.localeCompare(b));
  return (
    <Panel title={`Code input (${Object.keys(params).length} settings)`} wide actions={<input className="filter" placeholder="filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />}>
      <div className="scroll-box">
        <table className="grid compact">
          <tbody>
            {rows.map(([k, v]) => (
              <tr key={k}><td className="mono small">{k}</td><td className="mono small">{show(v)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
