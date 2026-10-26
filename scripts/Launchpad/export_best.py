#!/usr/bin/env python3
"""Write the best configurations found on Launchpad, with their measured
performance, to outputs/best/ (tracked in git; the raw logs are not).

    python scripts/Launchpad/export_best.py            # all benchmarks
    python scripts/Launchpad/export_best.py --top 20   # longer lists

One JSON file per benchmark plus README.md (a summary table):

* hpl_intel.json   Intel HPL tuner (smac3_intel_hpl*) top configs, and the
                   fixed-N large-N runs (hpl_bign/, system_ab/)
* hpl_netlib.json  netlib HPL tuner (smac3_launchpad_ranks)
* stream.json      CPU STREAM tuner (stream_smac3*)
* ngc_hpl.json     NGC GPU HPL runs (ngc_hpl/)
* ngc_stream.json  NGC GPU STREAM tuner (ngc_stream_smac3)
* mfc.json         best MFC config per case (mfc_smac3*, mfc_grid_*, mfc_igr_extra)
* nccl.json        best NCCL settings per collective (nccl_launchpad/tune_*.log)

Tuner entries are unique configurations (a run group's imported priors repeat
older runs; each configuration keeps its best result).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from typing import Any

from plot_style import AUTOTUNING, REPO, load_runs

OUTPUTS = REPO / "outputs"
BEST = OUTPUTS / "best"
#: Derived or host-local fields left out of the exported configurations / metrics.
DROP = {"array_memory_gib", "total_threads", "target_memory_bytes", "run_dir"}


def _groups(*prefixes: str) -> list[str]:
    return sorted(p.parent.name for p in AUTOTUNING.glob("*/evaluations.jsonl")
                  if p.parent.name.startswith(prefixes))


def _entry(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "objective": rec["objective"],
        "metrics": {k: v for k, v in (rec.get("metrics") or {}).items() if k not in DROP},
        "configuration": {k: v for k, v in rec["configuration"].items() if k not in DROP},
        "run_group": rec.get("run_group"),
        "attempt": rec.get("attempt"),
        "measured_at": rec.get("finished_at") or rec.get("submitted_at"),
    }


def top_configs(groups: list[str], top: int, key=None) -> list[dict[str, Any]]:
    """Best unique configurations over these run groups, best first. With
    ``key``, the best one per key(configuration) instead (e.g. per MFC case)."""
    best: dict[Any, dict[str, Any]] = {}
    for group in groups:
        for rec in load_runs(group):
            if not rec.get("success") or rec.get("objective") is None:
                continue
            e = _entry({**rec, "run_group": rec.get("run_group") or group})
            k = key(e["configuration"]) if key else json.dumps(e["configuration"], sort_keys=True)
            if k not in best or e["objective"] > best[k]["objective"]:
                best[k] = e
    ranked = sorted(best.values(), key=lambda e: -e["objective"])
    return ranked if key else ranked[:top]


def large_n_runs() -> list[dict[str, Any]]:
    """Fixed-config Intel HPL runs from hpl_bign.sh / system_ab.sh, best first."""
    rows = []
    for path, source in ((OUTPUTS / "hpl_bign/results.tsv", "hpl_bign"),
                         (OUTPUTS / "system_ab/results.tsv", "system_ab")):
        if not path.exists():
            continue
        for r in csv.DictReader(path.open(), delimiter="\t"):
            if r.get("check") != "PASSED":
                continue
            rows.append({"objective": float(r["gflops"]), "N": int(r["N"]), "arm": r["arm"], "source": source,
                         "seconds": float(r["secs"]),
                         "metrics": {k: r[k] for k in ("core_mhz_s0", "core_mhz_s1", "unc_mhz_s0", "unc_mhz_s1",
                                                      "pkg_w_s0", "pkg_w_s1", "ram_w_s0", "ram_w_s1", "tmax_c")
                                     if r.get(k) not in (None, "", "NA")}})
    return sorted(rows, key=lambda r: -r["objective"])


def ngc_hpl_runs() -> list[dict[str, Any]]:
    rows = []
    for log in sorted((OUTPUTS / "ngc_hpl").glob("*.log")):
        text = log.read_text(errors="replace")
        m = re.search(r"^W[RC]\S*\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.eE+]+)\s+\(\s*([\d.eE+]+)\)",
                      text, re.M)
        if not m or "PASSED" not in text:
            continue
        rows.append({"objective": float(m.group(6)), "per_gpu_gflops": float(m.group(7)),
                     "configuration": {"N": int(m.group(1)), "NB": int(m.group(2)),
                                       "P": int(m.group(3)), "Q": int(m.group(4))},
                     "seconds": float(m.group(5)), "log": str(log.relative_to(REPO))})
    return sorted(rows, key=lambda r: -r["objective"])


def nccl_best() -> list[dict[str, Any]]:
    logs = sorted((OUTPUTS / "nccl_launchpad").glob("tune_*.log"))
    if not logs:
        return []
    rows = []
    for m in re.finditer(r"^best (\w+): ([\d.]+) GB/s \((.*)\)\s+vs 16ch default ([\d.]+)",
                         logs[-1].read_text(), re.M):
        env = dict(kv.split("=", 1) for kv in m.group(3).split())
        rows.append({"collective": m.group(1), "objective": float(m.group(2)), "default_busbw_gb_s": float(m.group(4)),
                     "configuration": env, "log": str(logs[-1].relative_to(REPO))})
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--top", type=int, default=10, help="configurations per tuned benchmark (default 10)")
    top = p.parse_args().top
    BEST.mkdir(parents=True, exist_ok=True)

    hpl_groups = _groups("smac3_intel_hpl")
    stream_groups = _groups("stream_smac3")
    mfc_groups = _groups("mfc_")
    files: dict[str, dict[str, Any]] = {
        "hpl_intel": {"unit": "GFLOPS", "run_groups": hpl_groups, "top": top_configs(hpl_groups, top),
                      "large_n": large_n_runs()[:top]},
        "hpl_netlib": {"unit": "GFLOPS", "run_groups": _groups("smac3_launchpad"),
                       "top": top_configs(_groups("smac3_launchpad"), top)},
        "stream": {"unit": "Triad MB/s", "run_groups": stream_groups, "top": top_configs(stream_groups, top)},
        "ngc_hpl": {"unit": "GFLOPS (2x H100 NVL)", "top": ngc_hpl_runs()},
        "ngc_stream": {"unit": "Triad MB/s (one H100 NVL)", "run_groups": _groups("ngc_stream"),
                       "top": top_configs(_groups("ngc_stream"), top)},
        "mfc": {"unit": "throughput (1/grind time; grind_ns in metrics)", "run_groups": mfc_groups,
                "best_per_case": top_configs(mfc_groups, top, key=lambda c: c.get("case"))},
        "nccl": {"unit": "busbw GB/s (2x H100 NVL, NVLink)", "best_per_collective": nccl_best()},
    }
    for name, data in files.items():
        (BEST / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")

    def head(name: str, list_key: str = "top") -> dict[str, Any] | None:
        rows = files[name][list_key]
        return rows[0] if rows else None

    lines = ["# Best configurations", "",
             "Written by `scripts/Launchpad/export_best.py` from the raw logs in `outputs/` (which are not in git).", "",
             "| Benchmark | Best | Where | File |", "|---|---|---|---|"]
    h = head("hpl_intel")
    big = files["hpl_intel"]["large_n"]
    if big:
        lines.append(f"| Intel HPL, CPU, large N | {big[0]['objective']:,.1f} GFLOPS at N={big[0]['N']:,} | "
                     f"{big[0]['source']} | `hpl_intel.json` (`large_n`) |")
    if h:
        lines.append(f"| Intel HPL, CPU, tuner (N ~90k) | {h['objective']:,.1f} GFLOPS | {h['run_group']} attempt "
                     f"{h['attempt']} | `hpl_intel.json` (`top`) |")
    for name, label, scale, unit in (("hpl_netlib", "netlib HPL, CPU", 1, "GFLOPS"),
                                     ("stream", "STREAM, CPU (Triad)", 1e-3, "GB/s"),
                                     ("ngc_stream", "NGC STREAM, 1 GPU (Triad)", 1e-3, "GB/s")):
        h = head(name)
        if h:
            lines.append(f"| {label} | {h['objective'] * scale:,.1f} {unit} | {h['run_group']} attempt {h['attempt']} "
                         f"| `{name}.json` |")
    h = head("ngc_hpl")
    if h:
        lines.append(f"| NGC HPL, 2 GPUs | {h['objective'] / 1e3:,.2f} TFLOPS at N={h['configuration']['N']:,} | "
                     f"`{h['log']}` | `ngc_hpl.json` |")
    if files["mfc"]["best_per_case"]:
        lines.append(f"| MFC, 2 GPUs | best config for each of {len(files['mfc']['best_per_case'])} cases | "
                     f"{len(mfc_groups)} run groups | `mfc.json` |")
    for r in files["nccl"]["best_per_collective"]:
        lines.append(f"| NCCL {r['collective']} | {r['objective']:.2f} GB/s (default {r['default_busbw_gb_s']:.2f}) | "
                     f"`{r['log']}` | `nccl.json` |")
    (BEST / "README.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
