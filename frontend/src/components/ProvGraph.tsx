import cytoscape, { Core } from "cytoscape";
import dagre from "cytoscape-dagre";
import { useEffect, useRef } from "react";
import { Graph } from "../api";

cytoscape.use(dagre);

const KIND_COLORS: Record<string, string> = {
  dataset: "#2f6db3",
  model: "#8a4fb0",
  hpc_job: "#7a7f86",
  file: "#9aa0a6",
  md: "#c0622d",
  dft: "#2f8f5b",
  sampling: "#b08a1c",
  training: "#a8326e",
  import: "#9aa0a6",
  conversion: "#9aa0a6",
  other: "#6b7075",
};

function colorOf(n: Graph["nodes"][number]): string {
  if (n.kind === "calculation") return KIND_COLORS[n.calc_type ?? "other"] ?? KIND_COLORS.other;
  return KIND_COLORS[n.kind] ?? "#6b7075";
}

interface Props {
  graph: Graph;
  hideFiles: boolean;
  selected: string | null;
  onSelect: (id: string) => void;
  onReroot: (id: string) => void;
}

/** Provenance DAG: inputs at the top, products at the bottom (dagre layout). */
export default function ProvGraph({ graph, hideFiles, selected, onSelect, onReroot }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const hidden = new Set(
      graph.nodes.filter((n) => hideFiles && (n.kind === "file" || ["import", "conversion"].includes(n.calc_type ?? "")) && n.id !== graph.root).map((n) => n.id),
    );
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const fg = dark ? "#e3e5e7" : "#1d1f21";
    const elements = [
      ...graph.nodes.filter((n) => !hidden.has(n.id)).map((n) => ({
        data: {
          id: n.id,
          label: `${n.id}\n${n.name.length > 34 ? n.name.slice(0, 32) + "…" : n.name}${n.subtitle ? "\n" + (n.subtitle.length > 40 ? n.subtitle.slice(0, 38) + "…" : n.subtitle) : ""}`,
          color: colorOf(n),
          root: n.id === graph.root ? 1 : 0,
          placeholder: n.status === "placeholder" ? 1 : 0,
        },
      })),
      ...graph.edges
        .filter((e) => !hidden.has(e.src) && !hidden.has(e.dst))
        .map((e, i) => {
          // draw everything in data-flow direction: input → activity → output
          const flowFromDst = e.relation !== "generated";
          return {
            data: {
              id: `e${i}`,
              source: flowFromDst ? e.dst : e.src,
              target: flowFromDst ? e.src : e.dst,
              label: e.relation + (e.role ? ` [${e.role}]` : ""),
            },
          };
        }),
    ];
    cy.current?.destroy();
    const c = cytoscape({
      container: ref.current,
      elements,
      wheelSensitivity: 0.2,
      style: [
        {
          selector: "node",
          style: {
            shape: "round-rectangle",
            "background-color": "data(color)",
            "background-opacity": 0.14,
            "border-color": "data(color)",
            "border-width": 1.5,
            label: "data(label)",
            "text-wrap": "wrap",
            "text-valign": "center",
            "font-size": 10,
            "font-family": "ui-monospace, monospace",
            color: fg,
            width: 230,
            height: 52,
          },
        },
        { selector: "node[root = 1]", style: { "border-width": 3.5 } },
        { selector: "node[placeholder = 1]", style: { "border-style": "dashed" } },
        { selector: "node:selected", style: { "background-opacity": 0.4 } },
        {
          selector: "edge",
          style: {
            width: 1.2,
            "line-color": "#9aa0a6",
            "target-arrow-color": "#9aa0a6",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "data(label)",
            "font-size": 8,
            color: dark ? "#9097a0" : "#6b7075",
            "text-background-color": dark ? "#16181a" : "#fafafa",
            "text-background-opacity": 1,
            "text-rotation": "autorotate",
          },
        },
      ],
      layout: { name: "dagre", rankDir: "TB", nodeSep: 30, rankSep: 60 } as cytoscape.LayoutOptions,
    });
    c.on("tap", "node", (ev) => onSelect(ev.target.id()));
    c.on("dbltap", "node", (ev) => onReroot(ev.target.id()));
    cy.current = c;
    return () => c.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, hideFiles]);

  useEffect(() => {
    if (!cy.current || !selected) return;
    cy.current.nodes().unselect();
    cy.current.getElementById(selected).select();
  }, [selected]);

  return <div ref={ref} className="graph-canvas" />;
}

export const LEGEND = Object.entries({ dataset: "dataset", md: "MD", dft: "DFT", sampling: "sampling", training: "training", model: "model", hpc_job: "HPC job", file: "file / import" });
export { KIND_COLORS };
