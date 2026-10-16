#!/usr/bin/env python3
"""
Rank STREAM random-sweep JSONL results and summarize the strongest
validated configurations.

Usage:
  python3 stream_best.py outputs/stream/trials.jsonl
  python3 stream_best.py outputs/stream/trials.jsonl --top 25
  python3 stream_best.py outputs/stream/trials.jsonl --watch 30
  python3 stream_best.py outputs/stream/trials.jsonl --export outputs/stream/ranked.csv

Only successful runs with valid benchmark results are eligible.
The primary score is Triad bandwidth (MB/s).
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path


METRICS = (
    "triad_mb_s",
    "copy_mb_s",
    "scale_mb_s",
    "add_mb_s",
)

FACTORS = (
    "omp_threads",
    "stream_type",
    "element_bytes",
    "isa",
    "optimization",
    "streaming_stores",
    "memory_model",
    "kmp_affinity",
    "omp_proc_bind",
    "omp_places",
)


def number(row, key, default=None):
    """Read a numeric top-level field."""
    value = row.get(key)
    if value in ("", None):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def metric(row, key, default=None):
    """Read a numeric field from the nested metrics object."""
    metrics = row.get("metrics") or {}
    value = metrics.get(key)
    if value in ("", None):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_rows(path):
    """Read newline-delimited JSON, ignoring incomplete trailing records."""
    rows = []

    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                # A sweep may be writing its final record concurrently.
                print(
                    f"Warning: skipping malformed JSON at line "
                    f"{line_number}: {exc.msg}"
                )
                continue

            if isinstance(row, dict):
                rows.append(row)

    return rows


def valid_rows(rows):
    """Accept only successful, explicitly validated runs with Triad data."""
    eligible = []

    for row in rows:
        metrics = row.get("metrics") or {}

        validates = metrics.get("validates") is True or (
            isinstance(metrics.get("validates"), (int, float))
            and not isinstance(metrics.get("validates"), bool)
            and metrics["validates"] == 1
        )

        triad = metric(row, "triad_mb_s")

        if (
            row.get("status") == "ok"
            and row.get("return_code") == 0
            and validates
            and triad is not None
        ):
            eligible.append(row)

    return eligible


def show_row(i, row):
    triad = metric(row, "triad_mb_s", 0.0)

    print(
        f"{i:>3}. Triad {triad:>10,.1f} MB/s | "
        f"Copy {metric(row, 'copy_mb_s', 0):>10,.1f} | "
        f"Scale {metric(row, 'scale_mb_s', 0):>10,.1f} | "
        f"Add {metric(row, 'add_mb_s', 0):>10,.1f} | "
        f"threads={row.get('omp_threads', '?'):>3} "
        f"N={number(row, 'array_size', 0):,.0f} "
        f"type={row.get('stream_type', '?')} "
        f"ISA={row.get('isa', '?')} "
        f"affinity={row.get('kmp_affinity', '?')} "
        f"CPU-bind={row.get('numa_cpu_binding', '?')} "
        f"NUMA-mem={row.get('numa_memory_policy', '?')}"
    )

    print(
        f"     trial={row.get('trial', '?')} "
        f"status={row.get('status', '?')} "
        f"run={number(row, 'run_seconds', 0):.3f}s"
    )

    print(
        f"     flags: opt={row.get('optimization', '?')}; "
        f"stores={row.get('streaming_stores', '?')}; "
        f"model={row.get('memory_model', '?')}; "
        f"proc_bind={row.get('omp_proc_bind', '?')}; "
        f"places={row.get('omp_places', '?')}; "
        f"offset={row.get('offset', '?')}; "
        f"NTIMES={row.get('ntimes', '?')}"
    )


def summarize_factor(rows, factor, metric_name="triad_mb_s"):
    groups = defaultdict(list)

    for row in rows:
        value = metric(row, metric_name)
        if value is not None:
            groups[str(row.get(factor, "(missing)") )].append(value)

    if not groups:
        return

    print(f"\n{factor}:")
    print(f"{'value':<34} {'n':>5} {'median GB/s':>13} {'best GB/s':>12}")

    for value, values in sorted(
        groups.items(),
        key=lambda item: statistics.median(item[1]),
        reverse=True,
    ):
        print(
            f"{value:<34.34} {len(values):>5} "
            f"{statistics.median(values) / 1000:>13.2f} "
            f"{max(values) / 1000:>12.2f}"
        )


def export_csv(path, rows):
    """Flatten nested JSON fields into a convenient CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)

    flattened = []
    for row in rows:
        out = {}

        for key, value in row.items():
            if key == "metrics":
                continue
            if isinstance(value, (list, dict)):
                out[key] = json.dumps(value, separators=(",", ":"))
            else:
                out[key] = value

        for key, value in (row.get("metrics") or {}).items():
            out[key] = value

        flattened.append(out)

    # Include columns from every record, even if a field appears later.
    fields = list(dict.fromkeys(
        key for row in flattened for key in row
    ))

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=fields, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(flattened)


def main():
    parser = argparse.ArgumentParser(
        description="Rank validated STREAM JSONL trials."
    )
    parser.add_argument("jsonl", help="Path to the JSONL trial log")
    parser.add_argument(
        "--top", type=int, default=15,
        help="Number of top configurations to show"
    )
    parser.add_argument(
        "--watch", type=float, default=0,
        help="Refresh every N seconds until Ctrl-C"
    )
    parser.add_argument(
        "--export", default="",
        help="Optional path to export ranked valid trials as CSV"
    )
    args = parser.parse_args()

    if args.top < 1:
        parser.error("--top must be at least 1")
    if args.watch < 0:
        parser.error("--watch cannot be negative")

    path = Path(args.jsonl).expanduser()

    try:
        while True:
            if not path.is_file():
                raise SystemExit(f"Results file not found: {path}")

            rows = load_rows(path)
            valid = valid_rows(rows)

            print("\033[2J\033[H", end="")
            print(
                f"STREAM sweep results: {path} | "
                f"total rows={len(rows)} | "
                f"validated={len(valid)}"
            )

            statuses = defaultdict(int)
            for row in rows:
                statuses[row.get("status", "(missing)")] += 1

            print(
                "Statuses: "
                + ", ".join(
                    f"{status}={count}"
                    for status, count in sorted(statuses.items())
                )
            )

            if not valid:
                print("\nNo successful validated trials yet.")
            else:
                ranked = sorted(
                    valid,
                    key=lambda row: metric(row, "triad_mb_s", float("-inf")),
                    reverse=True,
                )

                print(f"\nTOP {min(args.top, len(ranked))} BY TRIAD BANDWIDTH")
                for i, row in enumerate(ranked[:args.top], 1):
                    show_row(i, row)

                print("\nBEST OBSERVED CONFIGURATION")
                show_row(1, ranked[0])

                print("\nFACTOR SUMMARIES (median and best Triad bandwidth)")
                for factor in FACTORS:
                    summarize_factor(valid, factor)

                if args.export:
                    export_path = Path(args.export).expanduser()
                    export_csv(export_path, ranked)
                    print(f"\nRanked CSV written to: {export_path}")

            if args.watch <= 0:
                break

            print(f"\nRefreshing in {args.watch:g}s; Ctrl-C to stop.")
            time.sleep(args.watch)

    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
