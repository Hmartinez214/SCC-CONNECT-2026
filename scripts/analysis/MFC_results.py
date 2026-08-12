#!/usr/bin/env python3
"""
parse_mfc_output.py

Parse MFC (Multi-component Flow Code) run output (the text mfc.sh prints to
stdout/the Slurm .out file when you run `./mfc.sh run ...`) and extract
performance / tuning-relevant metrics into a plain dict (and optionally JSON).

Usage:
    python3 parse_mfc_output.py job_624462.out
    python3 parse_mfc_output.py job_624462.out --json metrics.json
    python3 parse_mfc_output.py job_*.out --json all_jobs.json   # batch mode

Metrics extracted (all keys optional -- present only if found in the log):
    job:            partition, walltime, nodes, job_name, engine, binary,
                    queue_system, start_time, end_time, total_time_s, exit_code
    syscheck:       passed (bool)
    pre_process:    grid (nx, ny, nz), ranks, elapsed_s
    simulation:     grid, ranks, case_optimized (bool), n_steps,
                     final_perf_ns_per_gp_eq_rhs,
                     time_per_step_s: {mean, median, min, max, std, n},
                     wall_time_est_s (n_steps * mean time/step)
    post_process:   grid, ranks, n_saves, time_per_save_s: {mean, min, max}

Designed to be robust to minor formatting differences (extra whitespace,
escaped colons like "t\\_step" or "HH\\:MM\\:SS" that sometimes show up when
logs get copy-pasted through markdown/doc converters).
"""

import re
import sys
import json
import argparse
import statistics as stats
from pathlib import Path


def _norm(text: str) -> str:
    """Undo common escaping artifacts (backslash-escaped _ and :) so regexes
    written against the 'clean' MFC output also match doc-mangled logs."""
    return text.replace("\\_", "_").replace("\\:", ":").replace("\\*", "*")


def _grid_from_dims(nx: str, ny: str, nz: str) -> dict:
    nx, ny, nz = int(nx), int(ny), int(nz)
    return {"nx": nx, "ny": ny, "nz": nz, "total_cells": nx * ny * nz}


def parse_job_header(text: str) -> dict:
    out = {}
    fields = {
        "start_time": r"Start-time\s+(\S+)",
        "start_date": r"Start-date\s+(\S+)",
        "partition": r"Partition\s+(\S+)",
        "walltime": r"Walltime\s+(\S+)",
        "account": r"Account\s+(\S+)",
        "nodes": r"Nodes\s+(\d+)",
        "job_name": r"Job Name\s+(\S+)",
        "engine": r"Engine\s+(\S+)",
        "qos": r"QoS\s+(\S+)",
        "binary": r"Binary\s+(\S+)",
        "queue_system": r"Queue System\s+(\S+)",
        "email": r"Email\s+(\S+)",
    }
    for key, pat in fields.items():
        m = re.search(pat, text)
        if m:
            out[key] = m.group(1)
    if "nodes" in out:
        out["nodes"] = int(out["nodes"])

    m = re.search(r"Total-time:\s*(\d+)s", text)
    if m:
        out["total_time_s"] = int(m.group(1))
    m = re.search(r"Exit Code:\s*(\d+)", text)
    if m:
        out["exit_code"] = int(m.group(1))
    m = re.search(r"End-time:?\s+(\S+)", text)
    if m:
        out["end_time"] = m.group(1)
    return out


def parse_syscheck(text: str) -> dict:
    if re.search(r"Syscheck:\s*PASSED", text):
        return {"passed": True}
    if re.search(r"Syscheck:\s*FAILED", text):
        return {"passed": False}
    return {}


def parse_pre_process(text: str) -> dict:
    out = {}
    m = re.search(
        r"Pre-processing a (\d+)x(\d+)x(\d+) case on (\d+) rank\(s\)", text
    )
    if m:
        out.update(_grid_from_dims(m.group(1), m.group(2), m.group(3)))
        out["ranks"] = int(m.group(4))
    m = re.search(r"Elapsed Time\s+([\d.]+)", text)
    if m:
        out["elapsed_s"] = float(m.group(1))
    return out


def parse_simulation(text: str) -> dict:
    out = {}
    m = re.search(
        r"Simulating a (case-optimized )?(\d+)x(\d+)x(\d+) case on (\d+) rank\(s\)",
        text,
    )
    if m:
        out["case_optimized"] = bool(m.group(1))
        out.update(_grid_from_dims(m.group(2), m.group(3), m.group(4)))
        out["ranks"] = int(m.group(5))

    # Per-step lines, e.g.:
    # [ 34%]  Time step  300 of 901 @ t_step = 299  Time Avg = 6.24... Time/step= 6.24...E+00 ETA ...
    step_re = re.compile(
        r"Time step\s+(\d+)\s+of\s+(\d+).*?Time/step=\s*([\d.Ee+-]+)"
    )
    steps = [(int(a), int(b), float(c)) for a, b, c in step_re.findall(text)]
    if steps:
        n_total = steps[-1][1]
        times = [s[2] for s in steps]
        out["n_steps"] = n_total
        out["n_step_records_parsed"] = len(steps)
        out["time_per_step_s"] = {
            "mean": stats.mean(times),
            "median": stats.median(times),
            "min": min(times),
            "max": max(times),
            "std": stats.pstdev(times) if len(times) > 1 else 0.0,
            "n": len(times),
        }
        out["wall_time_est_s"] = out["time_per_step_s"]["mean"] * n_total

    m = re.search(r"Performance:\s*([\d.]+)\s*ns/gp/eq/rhs", text)
    if m:
        out["final_perf_ns_per_gp_eq_rhs"] = float(m.group(1))
    return out


def parse_post_process(text: str) -> dict:
    out = {}
    m = re.search(
        r"Post-processing a (\d+)x(\d+)x(\d+) case on (\d+) rank\(s\)", text
    )
    if m:
        out.update(_grid_from_dims(m.group(1), m.group(2), m.group(3)))
        out["ranks"] = int(m.group(4))

    save_re = re.compile(
        r"Saving\s+(\d+)\s+of\s+(\d+).*?Time/step\s*=\s*([\d.Ee+-]+)"
    )
    saves = [(int(a), int(b), float(c)) for a, b, c in save_re.findall(text)]
    if saves:
        n_total = saves[-1][1]
        times = [s[2] for s in saves if s[0] > 1]  # skip the 0th (0.0) sample
        out["n_saves"] = n_total
        if times:
            out["time_per_save_s"] = {
                "mean": stats.mean(times),
                "min": min(times),
                "max": max(times),
                "n": len(times),
            }
    return out


def parse_mfc_log(raw_text: str) -> dict:
    text = _norm(raw_text)
    result = {
        "job": parse_job_header(text),
        "syscheck": parse_syscheck(text),
        "pre_process": parse_pre_process(text),
        "simulation": parse_simulation(text),
        "post_process": parse_post_process(text),
    }
    # Drop empty sub-dicts for a cleaner result
    return {k: v for k, v in result.items() if v}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+", help="MFC .out log file(s) to parse")
    ap.add_argument("--json", metavar="PATH", help="write results as JSON to PATH")
    args = ap.parse_args()

    results = {}
    for f in args.files:
        path = Path(f)
        raw = path.read_text(errors="replace")
        metrics = parse_mfc_log(raw)
        results[path.name] = metrics

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
        print(f"Wrote {args.json}")
    else:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()