import { useMutation, useQuery } from "@tanstack/react-query";
import { get, post } from "../api";
import { ErrorBox, KV, Loading, NodeLink, Panel } from "../components/common";
import { fmtBytes } from "../format";

interface Store { root: string; db: string; db_size_bytes: number; files_size_bytes: number; instance_uuid: string; backups: { name: string; size_bytes: number }[] }
interface Validation { ok: boolean; errors: number; warnings: number; checked: Record<string, number>; problems: { severity: string; code: string; node: string | null; message: string }[] }

export default function IntegrityPage() {
  const store = useQuery({ queryKey: ["store"], queryFn: () => get<Store>("/store") });
  const val = useMutation({ mutationFn: (quick: boolean) => post<Validation>("/validate", undefined, { quick }) });
  const bak = useMutation({ mutationFn: () => post<{ archive: string; sha256: string; size_bytes: number }>("/backup"), onSuccess: () => store.refetch() });
  return (
    <section>
      <h1>Store &amp; integrity</h1>
      <div className="panels">
        <Panel title="Store">
          {store.isLoading && <Loading />}
          {store.data && (
            <KV rows={[
              ["root", <span className="mono small">{store.data.root}</span>],
              ["database", `${fmtBytes(store.data.db_size_bytes)}`],
              ["files", fmtBytes(store.data.files_size_bytes)],
              ["instance", <span className="mono small">{store.data.instance_uuid}</span>],
            ]} />
          )}
        </Panel>
        <Panel title="Validate">
          <p className="small muted">Read-only. Checks files, checksums, frame counts, provenance references, SQLite integrity and orphans.</p>
          <div className="toggles">
            <button className="btn" disabled={val.isPending} onClick={() => val.mutate(true)}>quick check</button>
            <button className="btn" disabled={val.isPending} onClick={() => val.mutate(false)}>full check (re-hash all files)</button>
          </div>
          {val.isPending && <Loading what="validation" />}
          {val.error && <ErrorBox error={val.error} />}
          {val.data && (
            <>
              <p><strong>{val.data.ok ? "✓ OK" : `✗ ${val.data.errors} error(s)`}</strong>, {val.data.warnings} warning(s) · checked {Object.entries(val.data.checked).map(([k, v]) => `${v} ${k}`).join(", ")}</p>
              <ul className="checklist">
                {val.data.problems.map((p, i) => (
                  <li key={i} className={p.severity === "error" ? "missing" : "ok"}>
                    [{p.severity}] {p.code} {p.node && <NodeLink id={p.node} />} — <span className="small">{p.message}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Panel>
        <Panel title="Backups">
          <button className="btn primary" disabled={bak.isPending} onClick={() => bak.mutate()}>create backup now</button>
          {bak.isPending && <Loading what="backup" />}
          {bak.error && <ErrorBox error={bak.error} />}
          {bak.data && <p className="small mono">{bak.data.archive}<br />sha256 {bak.data.sha256}</p>}
          <ul>
            {store.data?.backups.map((b) => <li key={b.name} className="mono small">{b.name} · {fmtBytes(b.size_bytes)}</li>)}
          </ul>
          <p className="small muted">Restore with <code>atomdb restore ARCHIVE NEW_DIR</code> (verifies every checksum).</p>
        </Panel>
      </div>
    </section>
  );
}
