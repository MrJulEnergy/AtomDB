import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { get } from "../api";

const DEST = "[DEST]";
const quote = (s: string) => (/^[\w@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`);

async function toClipboard(text: string) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  // plain-http access (e.g. via hostname instead of localhost) has no clipboard API
  const ta = document.createElement("textarea");
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  ta.remove();
}

/** `atomdb link` command for the given datasets; the user fills in [DEST] after pasting. */
export default function ShareCommand({ ids }: { ids: string[] }) {
  const health = useQuery({ queryKey: ["health"], queryFn: () => get<{ store: string }>("/health"), staleTime: Infinity });
  const [copy, setCopy] = useState(false);
  const [original, setOriginal] = useState(false);
  const [copied, setCopied] = useState(false);
  const cmd = [
    "atomdb",
    ...(health.data ? ["--home", quote(health.data.store)] : []),
    "link",
    ...(copy ? ["--copy"] : []),
    ...(original ? ["--original"] : []),
    ...ids,
    DEST,
  ].join(" ");
  return (
    <div className="share-cmd">
      <div className="toggles">
        <strong className="small">Get files:</strong>
        <label className="check"><input type="radio" checked={!copy} onChange={() => setCopy(false)} /> symlink (read-only)</label>
        <label className="check"><input type="radio" checked={copy} onChange={() => setCopy(true)} /> copy (writable)</label>
        <label className="check"><input type="checkbox" checked={original} onChange={(e) => setOriginal(e.target.checked)} /> original file instead of H5MD</label>
        <button onClick={() => toClipboard(cmd).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })}>
          {copied ? "copied ✓" : "copy command"}
        </button>
      </div>
      <pre className="code">{cmd}</pre>
      <p className="small muted">
        Replace {DEST} with the target directory after pasting{ids.length > 1 ? " (created if missing; clashing file names get the ID as prefix)" : ""}.
      </p>
    </div>
  );
}
