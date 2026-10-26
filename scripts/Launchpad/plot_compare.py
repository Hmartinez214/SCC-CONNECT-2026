#!/usr/bin/env python3
"""Compare autotuning runs: GFLOPS per run index and the best-so-far line for
each run group, overlaid (runs are counted per group, so lengths may differ).

    python scripts/Launchpad/plot_compare.py                     # HPL groups below
    python scripts/Launchpad/plot_compare.py smac3_launchpad_ranks smac3_intel_hpl
    python scripts/Launchpad/plot_compare.py --output plots/hpl/hpl_compare.png

A group name may carry a label after "=", e.g. smac3_intel_hpl="Intel HPL".
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from plot_style import GRID, SERIES, SURFACE, TEXT, TEXT_2, load_runs, plot_dir, style_axes  # noqa: E402

DEFAULT_GROUPS = [
    "smac3_launchpad_ranks=netlib HPL (spack), MPI-only",
    "smac3_intel_hpl=Intel HPL, small N",
    "smac3_intel_hpl_v2=Intel HPL, N up to 329k (+80 imported)",
    "smac3_intel_hpl_fast=Intel HPL, all knobs, small N (current; +89 imported)",
]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("groups", nargs="*", default=DEFAULT_GROUPS)
    p.add_argument("--metric", default="gflops")
    p.add_argument("--unit", default="GFLOPS")
    p.add_argument("--title", default="HPL autotuning runs: GFLOPS per run and best so far")
    p.add_argument("--output", default=None, help="PNG path (default: plots/<hpl|stream>/<hpl|stream>_compare.png)")
    args = p.parse_args()

    plt.rcParams.update({"font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": TEXT_2,
                         "xtick.color": TEXT_2, "ytick.color": TEXT_2})
    fig, ax = plt.subplots(figsize=(11, 6.5), dpi=150, facecolor=SURFACE)
    style_axes(ax)

    top, longest = 0.0, 0
    for color, spec in zip(SERIES, args.groups):
        group, _, label = spec.partition("=")
        records = load_runs(group)
        values = [float(r["metrics"].get(args.metric, r.get("objective"))) if r.get("success") else None
                  for r in records]
        index = list(range(1, len(values) + 1))
        best, running = float("-inf"), []
        for v in values:
            if v is not None:
                best = max(best, v)
            running.append(best if best > float("-inf") else float("nan"))
        ok = [(i, v) for i, v in zip(index, values) if v is not None]
        ax.scatter([i for i, _ in ok], [v for _, v in ok], s=10, color=color, alpha=0.3,
                   linewidths=0, zorder=2)
        ax.step(index, running, where="post", color=color, linewidth=2, zorder=3,
                label=f"{label or group}: best {best:,.0f}")
        ax.annotate(f"{best:,.0f}", (index[-1], running[-1]), xytext=(4, 0), textcoords="offset points",
                    va="center", color=TEXT, fontsize=9)
        top, longest = max(top, best), max(longest, len(index))

    ax.set_xlim(0.5, longest * 1.06)
    ax.set_ylim(0, top * 1.12)
    ax.set_xlabel("run index (within each run group)")
    ax.set_ylabel(args.unit)
    ax.set_title(args.title, loc="left", color=TEXT, fontsize=12, pad=12)
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.12), frameon=False, labelcolor=TEXT_2,
              title="line = best so far, dots = each run", title_fontsize=8, alignment="left")
    if args.output:
        out = Path(args.output)
    else:
        d = plot_dir(*(g.partition("=")[0] for g in args.groups))
        out = d / f"{d.name}_compare.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, facecolor=SURFACE)
    print(f"-> {out}")


if __name__ == "__main__":
    main()
