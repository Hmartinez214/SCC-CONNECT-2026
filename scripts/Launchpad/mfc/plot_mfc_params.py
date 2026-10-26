#!/usr/bin/env python3
"""Plot the effect of every tuned MFC variable, one figure each.

    python scripts/Launchpad/mfc/plot_mfc_params.py      # -> plots/mfc/params/<VARIABLE>.png, all_variables.png, summary.png

Reads every mfc_smac3* (SMAC) and mfc_grid_* (exhaustive) run group. Each run is compared with
the runs that have exactly the same settings except this variable, which is
at its default (off / none): y = their mean grind time / this run's grind
time. That isolates the one variable (case-opt alone doubles the speed, so a
plain "speedup over the plain run" would split every other variable's points
by the case-opt setting). Runs at the default value scatter around 1x and show
the run-to-run noise. Within each case, values are dodged (color + marker),
jittered, with the median per value as a black tick. Cases are rows
(benchmarks first, then examples), so the plots grow with the case count.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mfclib  # noqa: E402
from plot_style import AUTOTUNING, GRID, MARKERS, REPO, SERIES, SURFACE, TEXT, TEXT_2, load_runs  # noqa: E402

#: name -> (values, default first; title)
VARIABLES = {
    "CASE_OPT": (["off", "on"], "Case optimization (--case-optimization)"),
    "FASTMATH": (["off", "on"], "GPU fast math (-gpu=fastmath)"),
    "RDMA_MPI": (["off", "on"], "GPU-direct MPI (rdma_mpi)"),
    "BIND": (["none", "numa", "cores"], "Rank pinning (launchpad_bind.sh)"),
}


#: A case whose runs spread over more than this factor in grind time is left
#: out as unstable: the tuned variables move grind time by at most ~6x, so a
#: wider spread is run-to-run instability (seen on tiny 1D examples).
UNSTABLE_SPREAD = 10.0


def load(min_runs: int, keep_unstable: bool = False) -> dict[str, list[dict]]:
    """case -> successful runs ({"cfg", "grind"}), one mfc_smac3* group per
    case; benchmarks first, then examples, each by name. Cases with fewer than
    ``min_runs`` successful runs (still being tuned, or mostly failing) are left out."""
    out: dict[str, list[dict]] = {}
    # SMAC groups (mfc_smac3*) and exhaustive-grid groups (mfc_grid_*); a case
    # in both gets the runs of both.
    for d in sorted(list(AUTOTUNING.glob("mfc_smac3*")) + list(AUTOTUNING.glob("mfc_grid_*"))):
        if not (d / "evaluations.jsonl").exists():
            continue
        runs = [r for r in load_runs(d.name) if r.get("success")]
        if len(runs) >= min_runs:
            case = runs[0]["configuration"].get("case", d.name)
            out.setdefault(case, []).extend({"cfg": r["configuration"], "grind": r["metrics"]["grind_ns"]} for r in runs)
    if not keep_unstable:
        unstable = sorted(c for c, runs in out.items()
                          if max(r["grind"] for r in runs) > UNSTABLE_SPREAD * min(r["grind"] for r in runs))
        for c in unstable:
            print(f"left out {c}: grind time spread over {UNSTABLE_SPREAD:g}x between runs (unstable)")
            del out[c]
    examples = set(mfclib.example_slugs())
    return {c: out[c] for c in sorted(out, key=lambda c: (c in examples, c.lower()))}


def group(case: str) -> str:
    """benchmarks / 1D / 2D / 3D examples (other examples: "other")."""
    if case not in set(mfclib.example_slugs()):
        return "benchmarks"
    return case[:2] if case[:2] in ("1D", "2D", "3D") else "other"


GROUPS = ["benchmarks", "1D", "2D", "3D", "other"]


def summary(data: dict[str, list[dict]], path: Path) -> None:
    """One panel per variable: each case's median speedup for every
    non-default value, by case group, with the median over cases (black tick)
    and the most affected case named."""
    groups = [g for g in GROUPS if any(group(c) == g for c in data)]
    fig, axes = plt.subplots(2, 2, figsize=(15, 9), facecolor=SURFACE)
    rng = random.Random(0)
    for ax, (var, (values, title)) in zip(axes.flat, VARIABLES.items()):
        ax.set_facecolor(SURFACE)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        d = paired(data, var)
        rows = []
        for vi, v in list(enumerate(values))[1:]:
            for g in groups:
                meds = {c: statistics.median(r["speedup"] for r in runs if r["cfg"][var] == v)
                        for c, runs in d.items() if group(c) == g and any(r["cfg"][var] == v for r in runs)}
                if meds:
                    rows.append((vi, v, g, meds))
        for yi, (vi, v, g, meds) in enumerate(rows):
            xs = list(meds.values())
            ax.scatter(xs, [yi + rng.uniform(-0.22, 0.22) for _ in xs], s=16, color=SERIES[vi],
                       marker=MARKERS[vi], edgecolors=SURFACE, linewidths=0.5, alpha=0.85, zorder=2)
            m = statistics.median(xs)
            ax.plot([m, m], [yi - 0.36, yi + 0.36], color=TEXT, linewidth=1.8, zorder=3)
            far = max(meds, key=lambda c: abs(meds[c] - 1))
            if abs(meds[far] - 1) > 0.02:
                ax.annotate(short(far), (meds[far], yi), xytext=(4, 5), textcoords="offset points",
                            fontsize=6.5, color=TEXT_2)
        ax.axvline(1.0, color=TEXT_2, linewidth=0.8, linestyle=(0, (3, 3)), zorder=1)
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([f"{v}, {g} ({len(meds)})" for _, v, g, meds in rows], fontsize=8)
        ax.set_ylim(len(rows) - 0.5, -0.5)
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:g}×"))
        ax.set_title(title, loc="left", color=TEXT, fontsize=10)
        ax.set_xlabel(f"median speedup per case vs {var} = {values[0]}", fontsize=8)
    fig.suptitle(f"MFC on 2x H100: effect of each variable across {len(data)} cases "
                 "(dot = one case's median, black tick = median over cases)",
                 x=0.01, ha="left", color=TEXT, fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(path)


def paired(data: dict[str, list[dict]], var: str) -> dict[str, list[dict]]:
    """Runs with "speedup" over the same settings with ``var`` at its default."""
    default = VARIABLES[var][0][0]
    others = [k for k in VARIABLES if k != var]
    out = {}
    for case, runs in data.items():
        base: dict[tuple, list[float]] = {}
        for r in runs:
            if r["cfg"][var] == default:
                base.setdefault(tuple(r["cfg"][k] for k in others), []).append(r["grind"])
        rows = []
        for r in runs:
            b = base.get(tuple(r["cfg"][k] for k in others))
            if b:
                rows.append(dict(r, speedup=statistics.mean(b) / r["grind"]))
        if rows:
            out[case] = rows
    return out


def short(case: str) -> str:
    return case.replace("5eq_rk3_weno3_", "5eq ").replace("viscous_weno5_sgb_acoustic", "viscous+bubbles")


def draw(ax, var: str, data: dict[str, list[dict]], small: bool, labels: bool = True) -> None:
    """Horizontal strips: one row per case (top to bottom), speedup on x."""
    ax.set_facecolor(SURFACE)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    values = VARIABLES[var][0]
    cases = list(data)
    height = 0.8 / len(values)
    rng = random.Random(0)
    size = 12 if small else 22
    for vi, v in enumerate(values):
        for ci, case in enumerate(cases):
            xs = [r["speedup"] for r in data[case] if r["cfg"][var] == v]
            if not xs:
                continue
            y0 = ci - 0.4 + height * (vi + 0.5)
            ax.scatter(xs, [y0 + rng.uniform(-height * 0.25, height * 0.25) for _ in xs], s=size,
                       color=SERIES[vi], marker=MARKERS[vi], edgecolors=SURFACE, linewidths=0.5,
                       alpha=0.9, zorder=2)
            med = statistics.median(xs)
            ax.plot([med, med], [y0 - height * 0.42, y0 + height * 0.42], color=TEXT, linewidth=1.6, zorder=3)
    ax.axvline(1.0, color=TEXT_2, linewidth=0.8, linestyle=(0, (3, 3)), zorder=1)
    xs = [r["speedup"] for runs in data.values() for r in runs]
    lo, hi = min(xs + [1.0]), max(xs + [1.0])
    pad = (hi - lo) * 0.06 or 0.01
    ax.set_xlim(lo - pad, hi + pad)
    ax.set_yticks(range(len(cases)))
    ax.set_yticklabels([short(c) for c in cases], fontsize=7 if small else 8)
    if not labels:  # shared y axis: hide only this panel's labels
        ax.tick_params(labelleft=False)
    ax.set_ylim(len(cases) - 0.5, -0.5)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:g}×"))


def handles(var: str, data: dict[str, list[dict]], median: bool = True) -> list:
    hs = []
    for vi, v in enumerate(VARIABLES[var][0]):
        n = sum(1 for runs in data.values() for r in runs if r["cfg"][var] == v)
        hs.append(plt.Line2D([], [], linestyle="", marker=MARKERS[vi], color=SERIES[vi], markersize=6,
                             label=f"{v} ({n} runs)" + (" - baseline" if vi == 0 else "")))
    if median:
        hs.append(plt.Line2D([], [], color=TEXT, linewidth=1.6, label="median"))
    return hs


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--outdir", default=str(REPO / "plots" / "mfc" / "params"))
    p.add_argument("--min-runs", type=int, default=20,
                   help="leave out cases with fewer successful runs (default 20; a full case has ~24-30)")
    p.add_argument("--keep-unstable", action="store_true",
                   help=f"keep cases whose grind time spreads over {UNSTABLE_SPREAD:g}x between runs")
    args = p.parse_args()
    data = load(args.min_runs, args.keep_unstable)
    if not data:
        raise SystemExit("no MFC run groups with enough results")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": TEXT_2,
                         "xtick.color": TEXT_2, "ytick.color": TEXT_2})
    nruns = sum(len(r) for r in data.values())
    tall = 2.2 + 0.32 * len(data)
    xlabel = "speedup vs same settings with {} = {}"

    for var, (values, title) in VARIABLES.items():
        d = paired(data, var)
        fig, ax = plt.subplots(figsize=(9, tall), facecolor=SURFACE)
        draw(ax, var, d, small=False)
        fig.suptitle(f"MFC: effect of {title} ({nruns} runs, {len(data)} cases)",
                     x=0.01, ha="left", color=TEXT, fontsize=11)
        ax.set_xlabel(xlabel.format(var, values[0]))
        ax.legend(handles=handles(var, d), loc="lower left", bbox_to_anchor=(0, 1.0), ncol=4,
                  frameon=False, fontsize=8)
        fig.tight_layout(rect=(0, 0, 1, 1 - 0.5 / tall))
        fig.savefig(outdir / f"{var}.png", dpi=150, facecolor=SURFACE)
        plt.close(fig)
        print(outdir / f"{var}.png")

    fig, axes = plt.subplots(1, 4, figsize=(18, tall), facecolor=SURFACE, sharey=True)
    for i, (ax, (var, (values, title))) in enumerate(zip(axes, VARIABLES.items())):
        d = paired(data, var)
        draw(ax, var, d, small=True, labels=i == 0)
        ax.set_title(title, loc="left", color=TEXT, fontsize=10)
        ax.set_xlabel(xlabel.format(var, values[0]), fontsize=8)
        ax.legend(handles=handles(var, d, median=False), loc="upper left", bbox_to_anchor=(0, -0.09 * 12 / tall),
                  ncol=3, frameon=False, fontsize=7)
    fig.suptitle(f"MFC on 2x H100: effect of each variable ({nruns} runs, {len(data)} cases; black tick = median)",
                 x=0.01, ha="left", color=TEXT, fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 1 - 0.5 / tall))
    fig.savefig(outdir / "all_variables.png", dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(outdir / "all_variables.png")
    summary(data, outdir / "summary.png")


if __name__ == "__main__":
    main()
