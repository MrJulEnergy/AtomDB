import { ReactNode } from "react";
import { Link } from "react-router-dom";
import { nodeHref } from "../api";

export function NodeLink({ id, label }: { id: string; label?: string }) {
  return (
    <Link className="mono" to={nodeHref(id)}>
      {label ?? id}
    </Link>
  );
}

export function Loading({ what }: { what?: string }) {
  return <p className="muted">loading{what ? ` ${what}` : ""}…</p>;
}

export function ErrorBox({ error }: { error: unknown }) {
  return <p className="error">{error instanceof Error ? error.message : String(error)}</p>;
}

export function Panel({ title, children, wide, actions }: { title: string; children: ReactNode; wide?: boolean; actions?: ReactNode }) {
  return (
    <div className={`panel${wide ? " wide" : ""}`}>
      <div className="panel-head">
        <h2>{title}</h2>
        {actions}
      </div>
      {children}
    </div>
  );
}

export function KV({ rows }: { rows: [string, ReactNode][] }) {
  const shown = rows.filter(([, v]) => v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && v.length === 0));
  if (!shown.length) return <p className="muted">–</p>;
  return (
    <dl>
      {shown.map(([k, v]) => (
        <div className="kv" key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ProvButtons({ id }: { id: string }) {
  return (
    <div className="prov-buttons">
      <Link className="btn primary" to={`/provenance/${id}?dir=up`}>WHERE DID THIS COME FROM?</Link>
      <Link className="btn" to={`/provenance/${id}?dir=down`}>WHAT WAS THIS USED FOR?</Link>
    </div>
  );
}

export function Pager({ total, offset, limit, onChange }: { total: number; offset: number; limit: number; onChange: (o: number) => void }) {
  if (total <= limit) return null;
  return (
    <div className="pager">
      <button disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>‹ prev</button>
      <span className="muted">
        {offset + 1}–{Math.min(total, offset + limit)} of {total}
      </span>
      <button disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>next ›</button>
    </div>
  );
}

export function Labels({ e, f, s }: { e: boolean; f: boolean; s: boolean }) {
  return (
    <span className="labels">
      <span className={e ? "on" : "off"} title="energy">E</span>
      <span className={f ? "on" : "off"} title="forces">F</span>
      <span className={s ? "on" : "off"} title="stress">S</span>
    </span>
  );
}
