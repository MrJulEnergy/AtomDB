import { Chip } from "../api";

/** Short label (or name) plus auto chips; the full name is always one hover away. */
export default function DsName({ name, label, chips, full = false, hide = [] }: { name: string; label?: string | null; chips?: Chip[]; full?: boolean; hide?: Chip["kind"][] }) {
  const shown = label || name;
  chips = chips?.filter((c) => !hide.includes(c.kind));
  return (
    <span className="dsname">
      <span className="dsname-label" title={label ? name : undefined}>{shown}</span>
      {chips && chips.length > 0 && (
        <span className="chips">
          {chips.map((c, i) => <span key={i} className={`dchip dchip-${c.kind} dchip-${c.kind}-${c.text.replace(/\s+/g, "-")}`} title={c.title}>{c.text}</span>)}
        </span>
      )}
      {full && label && <span className="dsname-full muted small">{name}</span>}
    </span>
  );
}
