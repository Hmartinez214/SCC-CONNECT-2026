"""Shared bits for the plot_*.py scripts: paths, palette, and reading a run
group's evaluation log."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AUTOTUNING = REPO / "outputs" / "autotuning"

# Reference palette (dataviz skill). SERIES is categorical, used in this fixed
# order; MARKERS give each series a second cue besides color.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"]
MARKERS = ["o", "s", "^", "D", "v", "P", "X"]
BEST = SERIES[0]          # single-series "best so far" line
RUN = "#a3a29b"           # neutral ink for individual runs
TEXT, TEXT_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e8e7e2", "#fcfcfb"


def plot_dir(*run_groups: str) -> Path:
    """plots/stream for STREAM run groups, plots/hpl otherwise."""
    kind = "stream" if run_groups and all(g.startswith("stream") for g in run_groups) else "hpl"
    return REPO / "plots" / kind


def load_runs(run_group: str) -> list[dict]:
    """Finished runs of a run group, in attempt order (the final record per
    attempt; interrupted attempts that only have a SUBMITTED record are left out)."""
    path = AUTOTUNING / run_group / "evaluations.jsonl"
    final: dict[int, dict] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            final[int(rec.get("attempt", 0))] = rec
    return [final[a] for a in sorted(final) if final[a].get("status") != "SUBMITTED"]


def is_imported(record: dict) -> bool:
    """True for a run a run group seeded from an earlier group (experiment.priors)."""
    return bool((record.get("metadata") or {}).get("imported_from"))


def style_axes(ax) -> None:
    """Recessive grid and axes on the chart surface."""
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
