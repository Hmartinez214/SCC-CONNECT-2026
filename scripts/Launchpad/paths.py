"""Where Launchpad results live under outputs/ (see outputs/README.md).

One directory per benchmark: outputs/<bench>/ with the tuners' records in
autotuning/<run_group>/, each tuner run's application log in <run_group>/,
the job scripts and their stdout/stderr in slurm/, and runbest/ for the
runbest scripts. The node-wide lock is outputs/.node.lock.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUTPUTS = REPO / "outputs"
NODE_LOCK = OUTPUTS / ".node.lock"
BENCHES = ("hpl", "stream", "ngc", "mfc", "nccl", "wrf")

#: Run-group name prefix -> benchmark directory.
_PREFIXES = (("smac3_intel_hpl", "hpl"), ("smac3_launchpad", "hpl"), ("stream_smac3", "stream"),
             ("ngc_stream", "ngc"), ("mfc_", "mfc"), ("wrf_", "wrf"))


def bench_of(run_group: str) -> str:
    for prefix, bench in _PREFIXES:
        if run_group.startswith(prefix):
            return bench
    raise ValueError(f"run group {run_group!r} has no benchmark (add its prefix to paths._PREFIXES)")


def tuning_root(bench: str) -> Path:
    """The tuners' output root for a benchmark: records in <root>/<run_group>/;
    logs and job scripts go next to it (HPC-Tools uses <root>.parent)."""
    return OUTPUTS / bench / "autotuning"


def tuning_dir(run_group: str) -> Path:
    return tuning_root(bench_of(run_group)) / run_group


def log_dir(run_group: str) -> Path:
    return OUTPUTS / bench_of(run_group) / run_group


def slurm_dir(bench: str) -> Path:
    return OUTPUTS / bench / "slurm"


def run_groups(*prefixes: str) -> list[str]:
    """Run groups with an evaluations.jsonl, optionally only those starting with a prefix."""
    return sorted(p.parent.name for b in BENCHES for p in tuning_root(b).glob("*/evaluations.jsonl")
                  if not prefixes or p.parent.name.startswith(prefixes))


def note_lock_holder(what: str) -> None:
    """Record who holds the node lock (read by lib/node.sh lock_holder / bench status)."""
    (OUTPUTS / ".node.lock.holder").write_text(f"{os.getpid()}\t{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{what}\n")
