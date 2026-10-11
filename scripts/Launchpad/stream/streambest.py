#!/usr/bin/env python3
"""
Rank STREAM random-sweep results and summarize the strongest configurations.

Usage:
  python3 stream_best.py stream_random_sweeps/trials.csv
  python3 stream_best.py stream_random_sweeps/trials.csv --top 25
  python3 stream_best.py stream_random_sweeps/trials.csv --watch 30

Only successful, validated runs are eligible for the main ranking. The primary
score is Triad bandwidth (MB/s), a common STREAM comparison metric.
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
import time
from collections import defaultdict
from pathlib import Path

NUMERIC = [
    "triad_mb_s", "copy_mb_s", "scale_mb_s", "add_mb_s",
    "array_size", "ntimes", "offset", "omp_threads", "array_memory_gib",
    "build_seconds", "run_seconds",
]

def number(row, key, default=None):
    try:
        value = row.get(key, "")
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

def load_rows(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def valid_rows(rows):
    eligible = []
    for row in rows:
        triad = number(row, "triad_mb_s")
        validates = str(row.get("validates", "")).strip().lower() in ("true", "1", "yes")
        if row.get("status") == "ok" and validates and triad is not None:
            eligible.append(row)
    return eligible

def show_row(i, row):
    triad = number(row, "triad_mb_s", 0.0)
    print(
        f"{i:>3}. Triad {triad:>10,.1f} MB/s | "
        f"Copy {number(row, 'copy_mb_s', 0):>10,.1f} | "
        f"Scale {number(row, 'scale_mb_s', 0):>10,.1f} | "
        f"Add {number(row, 'add_mb_s', 0):>10,.1f} | "
        f"threads={row.get('omp_threads','?'):>3} "
        f"N={number(row,'array_size',0):,.0f} "
        f"type={row.get('stream_type','?')} "
        f"ISA={row.get('isa','?')} "
        f"affinity={row.get('kmp_affinity','?')} "
        f"CPU-bind={row.get('numa_cpu_binding','?')} "
        f"NUMA-mem={row.get('numa_memory_policy','?')}"
    )
    print(
        f"     flags: opt={row.get('optimization','?')}; "
        f"stores={row.get('streaming_stores','?')}; "
        f"model={row.get('memory_model','?')}; "
        f"proc_bind={row.get('omp_proc_bind','?')}; "
        f"places={row.get('omp_places','?')}; "
        f"offset={row.get('offset','?')}; NTIMES={row.get('ntimes','?')}"
    )

def summarize_factor(rows, factor, metric="triad_mb_s"):
    groups = defaultdict(list)
    for row in rows:
        value = number(row, metric)
        if value is not None:
            groups[row.get(factor, "(missing)")].append(value)
    if not groups:
        return
    print(f"\n{factor}:")
    print(f"{'value':<34} {'n':>5} {'median GB/s':>13} {'best GB/s':>12}")
    for value, vals in sorted(groups.items(), key=lambda kv: statistics.median(kv[1]), reverse=True):
        print(f"{value:<34.34} {len(vals):>5} {statistics.median(vals)/1000:>13.2f} {max(vals)/1000:>12.2f}")

def main():
    parser = argparse.ArgumentParser(description="Find the strongest validated STREAM trials.")
    parser.add_argument("csv", help="Path to trials.csv produced by stream_fuzz.py")
    parser.add_argument("--top", type=int, default=15, help="Number of top configurations to show")
    parser.add_argument("--watch", type=float, default=0, help="Refresh every N seconds until Ctrl-C")
    parser.add_argument("--export", default="", help="Optional path to export ranked valid trials as CSV")
    args = parser.parse_args()
    path = Path(args.csv).expanduser()

    try:
        while True:
            if not path.is_file():
                raise SystemExit(f"Results CSV not found: {path}")
            rows = load_rows(path)
            valid = valid_rows(rows)
            print("\033[2J\033[H", end="")  # clear terminal
            print(f"STREAM sweep results: {path} | total rows={len(rows)} | validated={len(valid)}")
            if not valid:
                print("\nNo successful validated trials yet.")
            else:
                ranked = sorted(valid, key=lambda r: number(r, "triad_mb_s", float("-inf")), reverse=True)
                print(f"\nTOP {min(args.top, len(ranked))} BY TRIAD BANDWIDTH")
                for i, row in enumerate(ranked[:max(1, args.top)], 1):
                    show_row(i, row)

                best = ranked[0]
                print("\nBEST OBSERVED CONFIGURATION")
                show_row(1, best)
                print("\nFACTOR SUMMARIES (median and best Triad bandwidth)")
                for factor in ("omp_threads", "isa", "optimization", "streaming_stores",
                               "kmp_affinity", "omp_proc_bind", "omp_places", "stream_type",
                               "numa_cpu_binding", "numa_memory_policy"):
                    summarize_factor(valid, factor)

                if args.export:
                    export_path = Path(args.export).expanduser()
                    export_path.parent.mkdir(parents=True, exist_ok=True)
                    fields = list(ranked[0].keys())
                    with export_path.open("w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                        writer.writeheader()
                        writer.writerows(ranked)
                    print(f"\nRanked CSV written to: {export_path}")

            if args.watch <= 0:
                break
            print(f"\nRefreshing in {args.watch:g}s; Ctrl-C to stop.")
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    main()
