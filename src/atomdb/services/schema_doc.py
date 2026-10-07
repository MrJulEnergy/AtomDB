"""Machine-readable description of atomdb for AI agents and scripts (``atomdb schema``)."""

from __future__ import annotations

from .. import __version__
from ..ids import PREFIXES
from .provenance import RELATIONS, UPSTREAM_REVERSED
from .search import ALIASES

ENTITIES = {
    "dataset": "frames of atomistic structures (+ labels); canonical data in an H5MD file",
    "file": "byte-identical file in the content-addressed store (sha256); many origins possible",
    "calculation": "activity: md | dft | sampling | training | conversion | import | other",
    "model": "interatomic potential (MLIP checkpoint or named foundation model)",
    "hpc_job": "a Slurm job (cluster, partition, job id, resources, state)",
}
WORKFLOWS = {
    "where did this file come from": ["atomdb whatis PATH --json",
                                       "atomdb provenance <ID> --up --json"],
    "what was this used for": ["atomdb provenance <ID> --down --json"],
    "find data": ["atomdb search 'r2scan+d4 el:K,Cl T:400' --json",
                  "atomdb search --element K --element Cl --frames --json"],
    "import a file with provenance": [
        "atomdb import FILE --temperature 400 --model MACE-MH-1 --source RUN_DIR --json"],
    "import an HPC run directory": ["atomdb import-run DIR --cluster NAME --json  (dry run)",
                                    "atomdb import-run DIR --cluster NAME --yes --json"],
    "import existing data": ["atomdb scan ROOT --plan plan.yaml --json",
                             "edit plan.yaml", "atomdb import-plan plan.yaml --json"],
    "add provenance later": ["atomdb annotate <DS> --set functional=r2SCAN --set dispersion=D4",
                             "atomdb relate <DS> sampled_from <DS>",
                             "atomdb record md|dft|sampling|training ..."],
    "find gaps": ["atomdb check --incomplete --json", "atomdb suggest --json"],
    "convert": ["atomdb convert IN OUT --json  (exit 6 if fields would be lost)"],
    "statistics": ["atomdb stats <DS> --json", "atomdb compare <DS> <DS> --json"],
    "integrity": ["atomdb validate --json", "atomdb backup --json"],
}


def schema(cli_commands: list[str] | None = None) -> dict:
    return {
        "atomdb_version": __version__,
        "id_format": "<PREFIX>-<YEAR>-<NNNNNN>, e.g. DS-2026-000142 (case-insensitive input)",
        "id_prefixes": {v: k for k, v in PREFIXES.items()},
        "entities": ENTITIES,
        "relations": {k: {"meaning": v, "direction": "src --relation--> dst",
                          "upstream_follows": "dst->src" if k in UPSTREAM_REVERSED else "src->dst"}
                      for k, v in RELATIONS.items()},
        "search_keys": sorted(set(ALIASES)),
        "search_examples": ["r2scan", "r2scan+d4", "T:400", "400 K", "T:300..400",
                            "model:MACE-MH-1", "el:K,Cl", "contains K and Cl", "el:K|Na",
                            "only:K,Cl,O,H", "es:any", "from:MD-2026-000017", "lineage:r2scan",
                            "frames>100", "has:stress"],
        "json_envelope": {"success": {"ok": True, "data": "..."},
                          "failure": {"ok": False, "error": {"code": "...", "message": "...",
                                                             "details": {}}}},
        "exit_codes": {"0": "ok", "1": "error", "2": "invalid_input", "3": "not_initialized",
                       "4": "not_found", "5": "format_error", "6": "data_loss", "7": "integrity"},
        "units": {"energy": "eV", "forces": "eV/Å", "stress": "eV/Å³", "length": "Å",
                  "time": "fs", "temperature": "K", "pressure": "bar", "cutoff": "Ry"},
        "workflows": WORKFLOWS,
        "cli_commands": cli_commands or [],
        "http_api": "atomdb serve → /api/v1 (OpenAPI at /api/v1/openapi.json); see docs/API.md",
    }
