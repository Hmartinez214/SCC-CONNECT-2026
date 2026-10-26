#!/usr/bin/env python3
"""Plot tuning runs with HPC-Tools' own plotting (hpc_autotuner.plotting).

    python scripts/Launchpad/plot_hpctools.py                  # -> plots/hpl/, plots/stream/
    python scripts/Launchpad/plot_hpctools.py --groups smac3_intel_hpl_fast --name hpl_fast

`hpc-tune plot --input outputs/autotuning` would put every run group on one
axis (STREAM's MB/s next to HPL's GFLOPS), so this picks groups per figure and
calls the same API: load_experiments() + plot_metric() for raw values and the
running best (cummax). Run groups seeded with priors repeat earlier groups'
runs at the start of their attempt numbering, which would draw exactly on top
of those groups; by default only each group's own runs are plotted
(--include-priors to keep them).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from hpc_autotuner.plotting import load_experiments, plot_metric  # noqa: E402

from plot_style import AUTOTUNING, REPO  # noqa: E402

HPL_GROUPS = ["smac3_launchpad_ranks", "smac3_intel_hpl", "smac3_intel_hpl_v2",
              "smac3_intel_hpl_v3", "smac3_intel_hpl_fast"]
STREAM_GROUPS = ["stream_smac3"]


def figure(groups: list[str], name: str, ylabel: str, outdir: Path, include_priors: bool = False) -> None:
    experiments = [e for e in load_experiments(AUTOTUNING) if e["name"] in groups]
    if not include_priors:
        for e in experiments:
            e["records"] = [r for r in e["records"] if not (r.get("metadata") or {}).get("imported_from")]
    experiments.sort(key=lambda e: groups.index(e["name"]))
    if not experiments:
        raise SystemExit(f"none of {groups} found under {AUTOTUNING}")
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    plot_metric(experiments, x="attempt", y="objective", aggregate="raw", ax=axes[0],
                title=f"{name}: {ylabel} per run")
    plot_metric(experiments, x="attempt", y="objective", aggregate="cummax", ax=axes[1],
                title=f"{name}: best {ylabel} so far")
    for ax in axes:
        ax.set_ylabel(ylabel)
    sub = outdir / ("stream" if all(g.startswith("stream") for g in groups) else "hpl")
    sub.mkdir(parents=True, exist_ok=True)
    out = sub / f"hpctools_{name}.png"
    fig.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"{name}: {', '.join(f'{e['name']} ({sum(1 for r in e['records'] if r.get('objective') is not None)} runs)' for e in experiments)} -> {out}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--groups", nargs="*", help="run groups to plot together (default: HPL and STREAM figures)")
    p.add_argument("--name", default="custom", help="figure name when --groups is given")
    p.add_argument("--ylabel", default="objective")
    p.add_argument("--outdir", default=str(REPO / "plots"))
    p.add_argument("--include-priors", action="store_true", help="also plot runs a group imported from earlier groups")
    args = p.parse_args()
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    if args.groups:
        figure(args.groups, args.name, args.ylabel, outdir, args.include_priors)
    else:
        figure(HPL_GROUPS, "hpl", "GFLOPS", outdir, args.include_priors)
        figure(STREAM_GROUPS, "stream", "Triad MB/s", outdir, args.include_priors)


if __name__ == "__main__":
    main()
