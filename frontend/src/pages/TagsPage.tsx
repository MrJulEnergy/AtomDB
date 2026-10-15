import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { get, post, send, type TagInfo, type TagKind, type TagList } from "../api";
import { ErrorBox, Loading, Panel } from "../components/common";

const KIND_TEXT: Record<TagKind, string> = {
  system: "system — what is simulated (chip + sidebar group)",
  project: "project — campaign / purpose (chip + sidebar group)",
  quality: "quality — warning about the data (red chip + sidebar group)",
  free: "free — searchable with tag:…, no chip",
};

interface Cleanup {
  redundant: { dataset: string; tag: string; reason: string }[];
  only_record: { dataset: string; tag: string; reason: string }[];
  contradicts: { dataset: string; tag: string; reason: string }[];
  deleted_tags?: string[];
  applied: boolean;
}

function TagRow({ t }: { t: TagInfo }) {
  const qc = useQueryClient();
  const [desc, setDesc] = useState(t.description ?? "");
  const [into, setInto] = useState("");
  const done = () => qc.invalidateQueries();
  const patch = useMutation({ mutationFn: (body: Record<string, string>) => send("PATCH", `/tags/${encodeURIComponent(t.name)}`, body), onSuccess: done });
  const merge = useMutation({ mutationFn: () => post(`/tags/${encodeURIComponent(t.name)}/merge`, { into }), onSuccess: done });
  const del = useMutation({ mutationFn: () => send("DELETE", `/tags/${encodeURIComponent(t.name)}`), onSuccess: done });
  const err = patch.error ?? merge.error ?? del.error;
  return (
    <tr>
      <td><span className={`dchip dchip-${t.kind === "quality" ? "warn" : t.kind}`}>{t.name}</span>
        {t.aliases.length > 0 && <div className="small muted">aliases: {t.aliases.join(", ")}</div>}</td>
      <td>
        <select value={t.kind} disabled={patch.isPending} onChange={(e) => patch.mutate({ kind: e.target.value })}>
          {(Object.keys(KIND_TEXT) as TagKind[]).map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
      </td>
      <td className="num">{t.count > 0 ? <Link to={`/?q=${encodeURIComponent(`tag:"${t.name}"`)}`}>{t.count}</Link> : 0}</td>
      <td><input className="wide" value={desc} placeholder="what does this tag mean?" onChange={(e) => setDesc(e.target.value)}
        onBlur={() => desc !== (t.description ?? "") && patch.mutate({ description: desc })} /></td>
      <td className="nowrap">
        <input list="tag-names" value={into} placeholder="merge into…" size={14} onChange={(e) => setInto(e.target.value)} />
        <button disabled={!into || merge.isPending} onClick={() => merge.mutate()}>merge</button>
        {t.count === 0 && <button disabled={del.isPending} onClick={() => del.mutate()}>delete</button>}
        {err && <ErrorBox error={err} />}
      </td>
    </tr>
  );
}

function CleanupPanel() {
  const qc = useQueryClient();
  const preview = useQuery({ queryKey: ["tag-cleanup"], queryFn: () => get<Cleanup>("/tags/cleanup") });
  const apply = useMutation({ mutationFn: () => post<Cleanup>("/tags/cleanup"), onSuccess: () => qc.invalidateQueries() });
  const group = (rows: Cleanup["redundant"]) => {
    const m = new Map<string, string[]>();
    for (const r of rows) m.set(`${r.tag} — ${r.reason}`, [...(m.get(`${r.tag} — ${r.reason}`) ?? []), r.dataset]);
    return [...m.entries()].sort();
  };
  const section = (title: string, rows: Cleanup["redundant"]) => rows.length > 0 && (
    <>
      <h3>{title} ({rows.length})</h3>
      <ul className="small">{group(rows).map(([k, ids]) => (
        <li key={k}>{k} <span className="muted">[{ids.map((i) => i.slice(-3)).join(", ")}]</span></li>
      ))}</ul>
    </>
  );
  return (
    <Panel title="Tags that repeat recorded facts">
      <p className="small muted">Geometry, codes, functionals, models … come from the frames and the provenance. Tags that only
        repeat them are <em>redundant</em>; tags that are the <em>only record</em> of something are kept (record it properly instead).</p>
      {preview.isLoading && <Loading />}
      {preview.error && <ErrorBox error={preview.error} />}
      {preview.data && (
        <>
          {section("Redundant — removed on apply", preview.data.redundant)}
          {section("Only record — kept", preview.data.only_record)}
          {section("Contradicts the data — kept", preview.data.contradicts)}
          {preview.data.redundant.length === 0 && <p>Nothing redundant.</p>}
          {preview.data.redundant.length > 0 && (
            <button className="btn" disabled={apply.isPending} onClick={() => apply.mutate()}>
              remove {preview.data.redundant.length} redundant tag links</button>
          )}
          {apply.error && <ErrorBox error={apply.error} />}
          {apply.data?.deleted_tags && <p className="small">deleted unused tags: {apply.data.deleted_tags.join(", ")}</p>}
        </>
      )}
    </Panel>
  );
}

export default function TagsPage() {
  const q = useQuery({ queryKey: ["tags"], queryFn: () => get<TagList>("/tags") });
  const names = (q.data?.tags ?? []).map((t) => t.name);
  return (
    <section>
      <h1>Tags</h1>
      <p className="muted small">Tags are labels, not facts. The kind decides how a tag is shown:</p>
      <ul className="small">{(Object.entries(KIND_TEXT) as [TagKind, string][]).map(([k, v]) => <li key={k}>{v}</li>)}</ul>
      {q.isLoading && <Loading />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && (
        <div className="table-wrap">
          <table className="grid">
            <thead><tr><th>Tag</th><th>Kind</th><th>Used</th><th>Description</th><th>Merge / delete</th></tr></thead>
            <tbody>
              {(["system", "project", "quality", "free"] as TagKind[]).flatMap((k) =>
                q.data.tags.filter((t) => t.kind === k).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
                  .map((t) => <TagRow key={`${t.name}-${t.kind}-${t.description}`} t={t} />))}
            </tbody>
          </table>
          <datalist id="tag-names">{names.map((n) => <option key={n} value={n} />)}</datalist>
        </div>
      )}
      <CleanupPanel />
    </section>
  );
}
