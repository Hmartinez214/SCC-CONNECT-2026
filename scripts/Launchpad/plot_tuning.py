#!/usr/bin/env python3
"""Plot an autotuning run: the objective of every run plus the running best.

    python scripts/Launchpad/plot_tuning.py                      # current HPL run group
    python scripts/Launchpad/plot_tuning.py --run-group stream_smac3 --metric triad_mb_s --scale 1e-3 --unit "GB/s"
    python scripts/Launchpad/plot_tuning.py --output plots/hpl.png

Reads outputs/autotuning/<run-group>/evaluations.jsonl (final record per
attempt). Failed / timed-out runs sit on the baseline as hollow rings;
imported prior results (if any) are shaded.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

# Reference palette (dataviz skill): one series hue, neutral ink for the rest.
BEST = "#2a78d6"
RUN = "#a3a29b"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e8e7e2"
SURFACE = "#fcfcfb"


def load(run_group: str) -> list[dict]:
    path = REPO / "outputs" / "autotuning" / run_group / "evaluations.jsonl"
    final: dict[int, dict] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("status") != "SUBMITTED":
                final[int(rec["attempt"])] = rec
    return [final[a] for a in sorted(final)]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--run-group", default="smac3_launchpad_ranks")
    p.add_argument("--metric", default="gflops", help="record field / metric to plot (default: gflops)")
    p.add_argument("--scale", type=float, default=1.0, help="multiply values by this (e.g. 1e-3 for MB/s -> GB/s)")
    p.add_argument("--unit", default="GFLOPS")
    p.add_argument("--title", default=None)
    p.add_argument("--output", default=None, help="PNG path (default: plots/<run-group>_progress.png)")
    args = p.parse_args()

    records = load(args.run_group)

    def value(rec: dict) -> float | None:
        if not rec.get("success"):
            return None
        v = rec.get("metrics", {}).get(args.metric, rec.get(args.metric, rec.get("objective")))
        return None if v is None else float(v) * args.scale

    attempts = [r["attempt"] for r in records]
    values = [value(r) for r in records]
    priors = [r for r in records if (r.get("metadata") or {}).get("imported_from")]

    best, running = float("-inf"), []
    for v in values:
        if v is not None:
            best = max(best, v)
        running.append(best if best > float("-inf") else None)

    ok = [(a, v) for a, v in zip(attempts, values) if v is not None]
    failed = [a for a, v in zip(attempts, values) if v is None]
    i_best = max(range(len(values)), key=lambda i: values[i] if values[i] is not None else float("-inf"))

    plt.rcParams.update({"font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": TEXT_2,
                         "xtick.color": TEXT_2, "ytick.color": TEXT_2})
    fig, ax = plt.subplots(figsize=(10, 5), dpi=150, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    if priors:
        last_prior = max(r["attempt"] for r in priors)
        ax.axvspan(0.5, last_prior + 0.5, color=GRID, alpha=0.5, linewidth=0)
        ax.text(1, 0.02, f"imported priors (1-{last_prior})", transform=ax.get_xaxis_transform(),
                color=TEXT_2, fontsize=8, va="bottom")

    ax.scatter([a for a, _ in ok], [v for _, v in ok], s=18, color=RUN, edgecolors=SURFACE,
               linewidths=0.6, zorder=2, label="each run")
    if failed:
        ax.scatter(failed, [0] * len(failed), s=22, facecolors="none", edgecolors=RUN,
                   linewidths=1, zorder=2, label="failed / timed out", clip_on=False)
    ax.step(attempts, [r if r is not None else float("nan") for r in running], where="post",
            color=BEST, linewidth=2, zorder=3, label="best so far")

    bv = values[i_best]
    if bv is not None:
        ax.scatter([attempts[i_best]], [bv], s=46, color=BEST, edgecolors=SURFACE, linewidths=1.5, zorder=4)
        ax.annotate(f"best {bv:,.1f} {args.unit} (run {attempts[i_best]})", (attempts[i_best], bv),
                    xytext=(0, 9), textcoords="offset points", ha="center", color=TEXT, fontsize=9)

    ax.set_ylim(0, (bv or 1) * 1.12)
    ax.set_xlim(0.5, max(attempts) + 0.5)
    ax.set_xlabel("run (attempt)")
    ax.set_ylabel(args.unit)
    title = args.title or f"{args.run_group}: {args.unit} per run"
    ax.set_title(title, loc="left", color=TEXT, fontsize=12, pad=12)
    ax.legend(loc="lower right", frameon=False, labelcolor=TEXT_2)

    out = Path(args.output) if args.output else REPO / "plots" / f"{args.run_group}_progress.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, facecolor=SURFACE)
    print(f"{len(records)} runs ({len(failed)} failed), best {bv:,.1f} {args.unit} at run {attempts[i_best]} -> {out}")


if __name__ == "__main__":
    main()
