import { useQuery } from "@tanstack/react-query";
import { get, type TagKind, type TagList } from "../api";

/** The tag registry (kinds, descriptions), shared by every component that shows tags. */
export function useTags() {
  return useQuery({ queryKey: ["tags"], queryFn: () => get<TagList>("/tags"), staleTime: 60_000 });
}

const CHIP: Record<TagKind, string> = { system: "dchip-system", project: "dchip-project", quality: "dchip-warn", free: "" };

/** Tags as chips coloured by their kind; free tags are plain. */
export default function TagChips({ names }: { names: string[] }) {
  const reg = useTags().data?.tags ?? [];
  const info = new Map(reg.map((t) => [t.name, t]));
  return (
    <span className="chips">
      {names.map((n) => {
        const t = info.get(n);
        return <span key={n} className={`dchip ${CHIP[t?.kind ?? "free"]}`} title={t ? `${t.kind} tag${t.description ? `: ${t.description}` : ""}` : "tag"}>{n}</span>;
      })}
    </span>
  );
}

/** <datalist id="all-tags"> with every known tag (for tag inputs). */
export function TagDatalist() {
  const reg = useTags().data?.tags ?? [];
  return <datalist id="all-tags">{reg.map((t) => <option key={t.name} value={t.name}>{t.kind}</option>)}</datalist>;
}
