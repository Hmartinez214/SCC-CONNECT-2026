#!/usr/bin/env python3
"""Plot HPL GFLOPS against every tuned variable (one small panel each).

    python scripts/Launchpad/plot_params.py                        # -> plots/hpl/params/<VARIABLE>.png
    python scripts/Launchpad/plot_params.py --groups smac3_intel_hpl_fast --outdir plots/hpl/params_fast

Each run appears once: a run group's imported priors are dropped, since they
are the earlier groups' runs. Runs that predate a knob are plotted at the
value they actually used (Intel's default). Numeric ranges (N, NB) are
scatter plots; the other variables are jittered strips with the median per
value. Each panel is a marginal view: other variables differ between runs,
so a gap between values is not by itself that variable's effect.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from plot_style import GRID, MARKERS, REPO, SERIES, SURFACE, TEXT, TEXT_2, is_imported, load_runs, style_axes  # noqa: E402

sys.path.insert(0, str(REPO / "scripts" / "Launchpad" / "hpl"))
from hpl_autotune import IntelHPLApplication  # noqa: E402

GROUPS = {
    "smac3_intel_hpl": "Intel HPL v1 (small N)",
    "smac3_intel_hpl_v2": "v2 (N up to 329k)",
    "smac3_intel_hpl_v3": "v3 (+uncore, env knobs)",
    "smac3_intel_hpl_fast": "fast (small N, all knobs)",
}
CONTINUOUS = ["N", "NB"]
LAUNCH = ["GRID", "MEM_POLICY", "UNCORE_MAX_MHZ", "HPL_HT", "HPL_NUMTHREADS", "HPL_PAIR", "HPL_UMODE",
          "HPL_SWAPWIDTH", "HPL_SWAPTHRESHOLD", "HPL_LARGEPAGE"]
HPLDAT = ["PMAP", "PFACT", "NBMIN", "NDIV", "RFACT", "BCAST", "DEPTH", "SWAP", "L1", "U", "EQUIL"]


def load(groups: dict[str, str]) -> list[dict]:
    """Each group's own successful runs (imported priors are earlier groups'
    runs); knobs a run predates are shown at Intel's default, what it used."""
    defaults = {k: d for k, (_, d) in IntelHPLApplication.LAUNCH_PARAMETERS.items()}
    runs = []
    for g in groups:
        if not (REPO / "outputs" / "autotuning" / g / "evaluations.jsonl").exists():
            continue
        for r in load_runs(g):
            if not is_imported(r) and r.get("success"):
                runs.append({"group": g, "gflops": float(r["objective"]), "cfg": {**defaults, **r["configuration"]}})
    return runs


def order(var: str, values: list) -> list:
    choices = IntelHPLApplication.LAUNCH_PARAMETERS.get(var, (None,))[0]
    seen = set(values)
    if choices:
        return [c for c in choices if c in seen] + sorted((v for v in seen if v not in choices), key=str)
    return sorted(seen, key=lambda v: (isinstance(v, str), v))


def draw(ax, var: str, runs: list[dict], groups: dict, style: dict, best: dict,
         ylim: tuple[float, float], rng: random.Random, small: bool) -> None:
    """One variable's panel: scatter for numeric ranges, jittered strip +
    median per value otherwise; the best run is circled."""
    style_axes(ax)
    size = 12 if small else 26
    if var in CONTINUOUS:
        for g in groups:
            pts = [(r["cfg"][var], r["gflops"]) for r in runs if r["group"] == g]
            if pts:
                c, m = style[g]
                ax.scatter([x for x, _ in pts], [y for _, y in pts], s=size + 2, color=c, marker=m,
                           edgecolors=SURFACE, linewidths=0.4, alpha=0.85, zorder=2)
        ax.ticklabel_format(axis="x", style="plain")
        bx = best["cfg"][var]
    else:
        cats = order(var, [r["cfg"][var] for r in runs])
        pos = {v: i for i, v in enumerate(cats)}
        for g in groups:
            pts = [(pos[r["cfg"][var]] + rng.uniform(-0.18, 0.18), r["gflops"]) for r in runs if r["group"] == g]
            if pts:
                c, m = style[g]
                ax.scatter([x for x, _ in pts], [y for _, y in pts], s=size, color=c, marker=m,
                           edgecolors=SURFACE, linewidths=0.4, alpha=0.85, zorder=2)
        for v in cats:
            med = statistics.median(r["gflops"] for r in runs if r["cfg"][var] == v)
            ax.plot([pos[v] - 0.3, pos[v] + 0.3], [med, med], color=TEXT, linewidth=1.6, zorder=3)
        ax.set_xticks(range(len(cats)))
        ax.set_xticklabels([f"{v}\nn={sum(1 for r in runs if r['cfg'][var] == v)}" for v in cats],
                           fontsize=7 if small else 9)
        ax.set_xlim(-0.6, len(cats) - 0.4)
        bx = pos[best["cfg"][var]]
    ax.scatter([bx], [best["gflops"]], s=60 if small else 110, facecolors="none", edgecolors=TEXT,
               linewidths=1.2, zorder=3)
    ax.set_ylim(*ylim)


def legend_handles(runs: list[dict], groups: dict, style: dict, best: dict) -> list:
    handles = [plt.Line2D([], [], linestyle="", marker=style[g][1], color=style[g][0], markersize=6,
                          label=f"{groups[g]} ({sum(1 for r in runs if r['group'] == g)} runs)")
               for g in groups if any(r["group"] == g for r in runs)]
    handles += [plt.Line2D([], [], color=TEXT, linewidth=1.6, label="median per value"),
                plt.Line2D([], [], linestyle="", marker="o", markerfacecolor="none", markeredgecolor=TEXT,
                           markersize=8, label=f"best run ({best['gflops']:.0f} GFLOPS)")]
    return handles


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--groups", nargs="*", default=list(GROUPS))
    p.add_argument("--outdir", default=str(REPO / "plots" / "hpl" / "params"),
                   help="one <VARIABLE>.png per variable plus all_variables.png (default: plots/hpl/params/)")
    args = p.parse_args()
    groups = {g: GROUPS.get(g, g) for g in args.groups}
    runs = load(groups)
    if not runs:
        raise SystemExit("no successful runs found")
    variables = CONTINUOUS + LAUNCH + HPLDAT
    style = {g: (SERIES[i], MARKERS[i]) for i, g in enumerate(groups)}
    best = max(runs, key=lambda r: r["gflops"])
    ys = [r["gflops"] for r in runs]
    pad = (max(ys) - min(ys)) * 0.08
    ylim = (min(ys) - pad, max(ys) + pad)
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": TEXT_2,
                         "xtick.color": TEXT_2, "ytick.color": TEXT_2})
    handles = legend_handles(runs, groups, style, best)

    # One figure per variable.
    for var in variables:
        fig, ax = plt.subplots(figsize=(8, 4.8), facecolor=SURFACE)
        draw(ax, var, runs, groups, style, best, ylim, random.Random(0), small=False)
        ax.set_title(f"Intel HPL: GFLOPS vs {var} ({len(runs)} runs)", loc="left", color=TEXT, fontsize=11, pad=8)
        ax.set_xlabel(var)
        ax.set_ylabel("GFLOPS")
        shown = [h for h in handles if not (var in CONTINUOUS and h.get_label() == "median per value")]
        ax.legend(handles=shown, loc="upper left", bbox_to_anchor=(0, -0.16), ncol=2, frameon=False,
                  fontsize=8, labelcolor=TEXT_2)
        fig.text(0.01, 0.005, "Marginal view: other variables differ between runs.", color=TEXT_2, fontsize=7)
        fig.tight_layout()
        fig.savefig(outdir / f"{var}.png", dpi=140, facecolor=SURFACE)
        plt.close(fig)

    # Overview grid of all variables.
    plt.rcParams.update({"font.size": 8})
    cols = 4
    rows = -(-len(variables) // cols)
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 4.2, rows * 2.6), sharey=True, facecolor=SURFACE)
    rng = random.Random(0)
    for ax, var in zip(axes.flat, variables):
        draw(ax, var, runs, groups, style, best, ylim, rng, small=True)
        ax.set_title(var, loc="left", color=TEXT, fontsize=9, pad=4)
    for ax in list(axes.flat)[len(variables):]:
        ax.set_visible(False)
    for ax in axes[:, 0]:
        ax.set_ylabel("GFLOPS")
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.01, 0.985), ncol=len(handles),
               frameon=False, fontsize=8, labelcolor=TEXT_2)
    fig.suptitle(f"Intel HPL: GFLOPS vs each tuned variable ({len(runs)} runs). "
                 "Marginal views: other variables differ between runs.",
                 x=0.01, y=0.998, ha="left", color=TEXT, fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    fig.savefig(outdir / "all_variables.png", dpi=140, facecolor=SURFACE)
    plt.close(fig)
    print(f"{len(runs)} runs: {len(variables)} per-variable plots + all_variables.png -> {outdir}")


if __name__ == "__main__":
    main()
