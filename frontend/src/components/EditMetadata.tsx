import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useState } from "react";
import { CalcSettings, CopyResult, DatasetSettings, get, send } from "../api";
import { ErrorBox, Loading, NodeLink, Panel } from "./common";
import { TagDatalist, useTags } from "./TagChips";

// Labels and units for the typed settings (keys are the database columns).
const LABELS: Record<string, string> = {
  code: "Code", code_version: "Code version", functional: "Functional", dispersion: "Dispersion",
  basis: "Basis set", pseudopotential: "Pseudopotential", cutoff_ry: "Cutoff [Ry]",
  rel_cutoff_ry: "Rel. cutoff [Ry]", eps_scf: "EPS_SCF", smearing: "Smearing", charge: "Charge",
  multiplicity: "Multiplicity", spin_polarized: "Spin polarized (true/false)", kpoints: "k-points",
  labels: "Computed properties (comma-sep.)",
  engine: "Engine", engine_version: "Engine version", ensemble: "Ensemble", temperature_k: "Temperature [K]",
  pressure_bar: "Pressure [bar]", timestep_fs: "Timestep [fs]", n_steps: "Steps", length_ps: "Length [ps]",
  thermostat: "Thermostat", barostat: "Barostat", integrator: "Integrator", seed: "Seed",
  density_g_cm3: "Density [g/cm³]", pbc: "PBC", initial_velocities: "Initial velocities", save_interval: "Save interval",
};
const HIDDEN = new Set(["functional_raw"]);
const COPY_PARTS = [["dft", "DFT settings"], ["md", "MD settings"], ["attrs", "metadata"], ["tags", "tags"]] as const;

export const toText = (v: unknown) => (v === null || v === undefined ? "" : Array.isArray(v) ? v.join(",") : String(v));

/** Refresh everything shown after an edit (facets, provenance, names, ...). */
function useRefresh() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries();
}

/** Text inputs for each field; returns only the values that were changed. */
function FieldGrid({ fields, values, onChange }: { fields: string[]; values: Record<string, string>; onChange: (k: string, v: string) => void }) {
  return (
    <div className="form-grid">
      {fields.filter((f) => !HIDDEN.has(f)).map((f) => (
        <label key={f}>
          <span>{LABELS[f] ?? f}</span>
          <input value={values[f] ?? ""} onChange={(e) => onChange(f, e.target.value)} spellCheck={false} />
        </label>
      ))}
    </div>
  );
}

function changed(initial: Record<string, string>, current: Record<string, string>) {
  return Object.fromEntries(Object.entries(current).filter(([k, v]) => v !== (initial[k] ?? "")));
}

// --------------------------------------------------------------------------- general fields

export function GeneralEditor({ node }: { node: { id: string; name: string; label?: string | null; description?: string | null; tags?: string[]; attrs?: Record<string, { value: unknown }> } }) {
  const refresh = useRefresh();
  const attrs0 = Object.entries(node.attrs ?? {}).map(([k, v]) => [k, typeof v.value === "string" ? v.value : JSON.stringify(v.value)] as [string, string]);
  const [name, setName] = useState(node.name);
  const [label, setLabel] = useState(node.label ?? "");
  const [description, setDescription] = useState(node.description ?? "");
  const [tags, setTags] = useState((node.tags ?? []).join(", "));
  const knownTags = new Set((useTags().data?.tags ?? []).map((x) => x.name));
  const [note, setNote] = useState("");
  const [attrs, setAttrs] = useState<[string, string][]>(attrs0);
  const m = useMutation({
    mutationFn: () => {
      const want = Object.fromEntries(attrs.filter(([k]) => k.trim()));
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(want)) if (attrs0.find(([a]) => a === k)?.[1] !== v) out[k] = parseValue(v);
      for (const [k] of attrs0) if (!(k in want)) out[k] = null;
      return send<{ changes: Record<string, unknown> }>("PATCH", `/nodes/${node.id}`, {
        name, label, description, note, attrs: out,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      });
    },
    onSuccess: () => { setNote(""); refresh(); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); m.mutate(); };
  return (
    <form onSubmit={submit}>
      <div className="form-grid">
        <label className="full"><span>Name</span><input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label><span>Label (short)</span><input value={label} maxLength={64} onChange={(e) => setLabel(e.target.value)}
          placeholder="e.g. KCl/water clusters 8 Å — method is shown as chips" /></label>
        <label><span>Tags (comma-sep.)</span><input value={tags} onChange={(e) => setTags(e.target.value)} />
          <input list="all-tags" placeholder="add a known tag…"
            onChange={(e) => {
              const v = e.target.value.trim();
              if (!knownTags.has(v)) return; // only when a suggestion was picked / typed in full
              const cur = tags.split(",").map((x) => x.trim()).filter(Boolean);
              if (!cur.includes(v)) setTags([...cur, v].join(", "));
              e.target.value = "";
            }} />
          <TagDatalist /></label>
        <label className="full"><span>Description</span><textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <label className="full"><span>Add note</span><input value={note} onChange={(e) => setNote(e.target.value)} placeholder="free text, appended to the notes" /></label>
      </div>
      <h3>Metadata</h3>
      <div className="attr-rows">
        {attrs.map(([k, v], i) => (
          <div key={i} className="attr-row">
            <input placeholder="key" value={k} onChange={(e) => setAttrs(attrs.map((a, j) => (j === i ? [e.target.value, a[1]] : a)))} />
            <input placeholder="value" value={v} onChange={(e) => setAttrs(attrs.map((a, j) => (j === i ? [a[0], e.target.value] : a)))} />
            <button type="button" title="remove" onClick={() => setAttrs(attrs.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        <button type="button" onClick={() => setAttrs([...attrs, ["", ""]])}>+ add field</button>
      </div>
      <SaveRow pending={m.isPending} error={m.error} done={m.isSuccess ? m.data.changes : undefined} />
    </form>
  );
}

/** Numbers and JSON stay typed; everything else is a string. */
function parseValue(v: string): unknown {
  const t = v.trim();
  if (t !== "" && !isNaN(Number(t))) return Number(t);
  if (/^[[{]/.test(t) || t === "true" || t === "false") {
    try { return JSON.parse(t); } catch { /* keep as string */ }
  }
  return v;
}

function SaveRow({ pending, error, done, label = "Save" }: { pending: boolean; error: unknown; done?: Record<string, unknown>; label?: string }) {
  return (
    <div className="save-row">
      <button type="submit" className="primary" disabled={pending}>{pending ? "saving…" : label}</button>
      {error ? <ErrorBox error={error} /> : null}
      {done && <span className="muted small">{Object.keys(done).length ? `saved: ${Object.keys(done).join(", ")}` : "nothing changed"}</span>}
    </div>
  );
}

// --------------------------------------------------------------------------- DFT / MD settings

function SettingsEditor({ dsId, section, fields, calcs }: { dsId: string; section: "dft" | "md"; fields: string[]; calcs: CalcSettings[] }) {
  const refresh = useRefresh();
  const [pick, setPick] = useState(0);
  const calc = calcs[pick] as CalcSettings | undefined;
  const initial = Object.fromEntries(fields.map((f) => [f, toText(calc?.values[f])]));
  const [values, setValues] = useState(initial);
  const [model, setModel] = useState(calc?.model ?? "");
  const [input, setInput] = useState("");
  useEffect(() => { setValues(initial); setModel(calc?.model ?? ""); }, [calc?.calc_id, JSON.stringify(calc?.values), calc?.model]);
  const m = useMutation({
    mutationFn: () => send<{ calc_id: string; created: boolean; changes: Record<string, unknown> }>(
      "PUT", `/datasets/${dsId}/settings/${section}`, {
        values: calc ? changed(initial, values) : values,
        inputs: input.trim() ? input.split(/[\s,]+/).filter(Boolean) : [],
        model: section === "md" && model !== (calc?.model ?? "") ? model : null,
        calc_id: calc?.calc_id ?? null,
      }),
    onSuccess: () => { setInput(""); refresh(); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); m.mutate(); };
  return (
    <form onSubmit={submit}>
      {calcs.length > 1 && (
        <select value={pick} onChange={(e) => setPick(Number(e.target.value))}>
          {calcs.map((c, i) => <option key={c.calc_id} value={i}>{c.calc_id} {c.name}</option>)}
        </select>
      )}
      <p className="small muted">
        {calc ? <>Editing <NodeLink id={calc.calc_id} /> {calc.name}. Changes apply in place and are logged.</>
          : <>No {section.toUpperCase()} calculation recorded yet — saving creates one.</>}
      </p>
      <FieldGrid fields={fields} values={values} onChange={(k, v) => setValues({ ...values, [k]: v })} />
      <div className="form-grid">
        {section === "md" && (
          <label><span>Driving model (name or ID)</span><input value={model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. MACE-MH-1" /></label>
        )}
        <label>
          <span>{section === "dft" ? "Input structures (dataset IDs it was labeled from)" : "Input (start structure dataset IDs)"}</span>
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="DS-2026-…" />
        </label>
      </div>
      {calc && calc.shared_with.length > 0 && (
        <p className="small error">This calculation also produced {calc.shared_with.map((i) => <NodeLink key={i} id={i} />)} — changes apply to those too.</p>
      )}
      {calc && calc.inputs.length > 0 && <p className="small muted">inputs: {calc.inputs.map((i) => <NodeLink key={i} id={i} />)}</p>}
      <SaveRow pending={m.isPending} error={m.error} done={m.isSuccess ? m.data.changes : undefined}
        label={calc ? "Save" : `Record ${section.toUpperCase()} settings`} />
    </form>
  );
}

// --------------------------------------------------------------------------- copying

export function CopyForm({ targets, source: source0 = "", onDone }: { targets?: string[]; source?: string; onDone?: () => void }) {
  const refresh = useRefresh();
  const [source, setSource] = useState(source0);
  const [others, setOthers] = useState("");
  const [parts, setParts] = useState<string[]>(["dft", "md", "attrs", "tags"]);
  // fixed targets (dataset page / selection) or a free list of IDs when copying *from* this dataset
  const tgts = targets ?? others.split(/[\s,]+/).filter(Boolean);
  const m = useMutation({
    mutationFn: () => send<CopyResult>("POST", "/copy-metadata", { source, targets: tgts, parts }),
    onSuccess: () => { refresh(); onDone?.(); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); m.mutate(); };
  return (
    <form onSubmit={submit} className="copy-form">
      <div className="form-grid">
        {targets ? (
          <label><span>Copy from dataset</span><input value={source} onChange={(e) => setSource(e.target.value)} placeholder="DS-2026-…" required /></label>
        ) : (
          <label className="full"><span>Apply to datasets (IDs, space/comma separated)</span>
            <textarea rows={2} value={others} onChange={(e) => setOthers(e.target.value)} placeholder="DS-2026-000004 DS-2026-000005 …" required /></label>
        )}
      </div>
      <div className="toggles">
        {COPY_PARTS.map(([k, label]) => (
          <label key={k} className="check">
            <input type="checkbox" checked={parts.includes(k)} onChange={(e) => setParts(e.target.checked ? [...parts, k] : parts.filter((p) => p !== k))} /> {label}
          </label>
        ))}
      </div>
      <p className="small muted">
        Copies the settings, full code input and config files of the source's DFT/MD calculation. Each target gets its own
        calculation (an existing one is overwritten with the copied values). Input structures are not copied.
      </p>
      <div className="save-row">
        <button type="submit" className="primary" disabled={m.isPending || !source || !tgts.length || !parts.length}>
          {m.isPending ? "copying…" : `Copy to ${tgts.length} dataset${tgts.length === 1 ? "" : "s"}`}
        </button>
        {m.error ? <ErrorBox error={m.error} /> : null}
        {m.isSuccess && <span className="muted small">copied to {m.data.results.length} dataset(s)</span>}
        {m.isSuccess && m.data.results.some((r) => r.also_affects?.length) && (
          <span className="small error">also changed (shared calculation): {[...new Set(m.data.results.flatMap((r) => r.also_affects ?? []))].join(", ")}</span>
        )}
      </div>
    </form>
  );
}

// --------------------------------------------------------------------------- dataset page

export default function DatasetEditor({ ds }: { ds: { id: string; name: string; description: string | null; tags: string[] } }) {
  const st = useQuery({ queryKey: ["settings", ds.id], queryFn: () => get<DatasetSettings>(`/datasets/${ds.id}/settings`) });
  const node = useQuery({ queryKey: ["node", ds.id], queryFn: () => get<{ attrs: Record<string, { value: unknown }> }>(`/nodes/${ds.id}`) });
  if (st.isLoading || node.isLoading) return <Loading what="editor" />;
  if (st.error) return <ErrorBox error={st.error} />;
  const s = st.data!;
  return (
    <div className="panels editor">
      <Panel title="Edit: name, tags, metadata">
        <GeneralEditor node={{ ...ds, attrs: node.data?.attrs }} />
      </Panel>
      <Panel title="Copy metadata">
        <h3>From another dataset into this one</h3>
        <CopyForm targets={[ds.id]} />
        <h3>From this dataset to others</h3>
        <CopyForm source={ds.id} />
      </Panel>
      <Panel title="DFT settings (labels)" wide>
        <SettingsEditor dsId={ds.id} section="dft" fields={s.fields.dft} calcs={s.dft} />
      </Panel>
      <Panel title="MD settings (how the structures were generated)" wide>
        <SettingsEditor dsId={ds.id} section="md" fields={s.fields.md} calcs={s.md} />
      </Panel>
    </div>
  );
}

// --------------------------------------------------------------------------- calculation page

export function CalcSettingsEditor({ calcId, section, values, model }: { calcId: string; section: "dft" | "md"; values: Record<string, unknown>; model?: string | null }) {
  const refresh = useRefresh();
  const fields = Object.keys(LABELS).filter((k) => (section === "dft" ? DFT_FIELDS : MD_FIELDS).includes(k));
  const initial = Object.fromEntries(fields.map((f) => [f, toText(values[f])]));
  const [vals, setVals] = useState(initial);
  const [mdl, setMdl] = useState(model ?? "");
  useEffect(() => { setVals(initial); setMdl(model ?? ""); }, [JSON.stringify(values), model]);
  const m = useMutation({
    mutationFn: () => send<{ changes: Record<string, unknown> }>("PATCH", `/nodes/${calcId}`, {
      [section]: changed(initial, vals), ...(section === "md" && mdl !== (model ?? "") ? { model: mdl } : {}),
    }),
    onSuccess: refresh,
  });
  return (
    <form onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
      <FieldGrid fields={fields} values={vals} onChange={(k, v) => setVals({ ...vals, [k]: v })} />
      {section === "md" && (
        <div className="form-grid">
          <label><span>Driving model (name or ID)</span><input value={mdl} onChange={(e) => setMdl(e.target.value)} /></label>
        </div>
      )}
      <SaveRow pending={m.isPending} error={m.error} done={m.isSuccess ? m.data.changes : undefined} />
    </form>
  );
}

const DFT_FIELDS = ["code", "code_version", "functional", "dispersion", "basis", "pseudopotential", "cutoff_ry", "rel_cutoff_ry",
  "eps_scf", "smearing", "charge", "multiplicity", "spin_polarized", "kpoints", "labels"];
const MD_FIELDS = ["engine", "engine_version", "ensemble", "temperature_k", "pressure_bar", "timestep_fs", "n_steps", "length_ps",
  "thermostat", "barostat", "integrator", "seed", "density_g_cm3", "pbc", "initial_velocities", "save_interval"];
