#!/usr/bin/env python3
"""WRF application for the Launchpad SMAC3 autotuner (see ../autotune_local.py).

Each evaluation runs the Santa Cruz case (9 km d01 + 3 km d02 nest,
100x100x45 each, 3-hour forecast, no history output) through runwrf.py with
the chosen build and launch settings. The objective is ``speed``: simulated
seconds per wall second over every d01 step after the first two (each d01
step includes its three d02 steps). Failed runs score 0.

Space (YAML ``tunable`` picks the searched ones):

* BUILD     gnu (dmpar, -O2) | gnu_dmsm (MPI+OpenMP, -O3 -march=sapphirerapids)
* RANKS     MPI ranks. WRF needs >= 10 points per patch side, so the
            100-point domains allow at most 9-10 ranks per direction.
* THREADS   OpenMP threads per rank (gnu: 1 only). RANKS x THREADS <= 128;
            above 64 the extra slots are hyperthreads.
* TILES     numtiles per patch (cache blocking / OpenMP work units)
* DECOMP    auto (WRF's choice) | wide (nproc_x >= nproc_y) | tall
* BIND      core | socket | numa | none  (Open MPI 4.1 --map-by/--bind-to)
* UNCORE_MAX_MHZ, EPB, THP   machine-wide, set per job and restored after

Usage (see wrf_smac3.sh):

    python scripts/Launchpad/wrf/wrf_autotune.py --config scripts/Launchpad/wrf/wrf_smac3.yaml
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

from hpc_autotuner.applications.base import Application
from hpc_autotuner.core.parameter import Parameter
from hpc_autotuner.experiments import common
from hpc_autotuner.experiments.config import ExperimentConfig

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import autotune_local  # noqa: E402

RUNWRF = "scripts/Launchpad/wrf/runwrf.py"
RESULT_TAG = "WRF_RESULT "


class WRFApplication(Application):
    """WRF ARW on the two Xeon 6548Y+, tuned on simulation speed."""

    objective_metric = "speed"
    objective_direction = "maximize"

    PARAMETERS: dict[str, tuple[list[Any], Any]] = {
        "BUILD": (["gnu", "gnu_dmsm"], "gnu"),
        "RANKS": ([16, 20, 24, 30, 32, 36, 40, 42, 48, 49, 54, 56, 60, 63, 64, 72, 80, 81], 64),
        "THREADS": ([1, 2, 3, 4, 6, 8], 1),
        "TILES": ([1, 2, 4, 8, 16], 1),
        "DECOMP": (["auto", "wide", "tall"], "auto"),
        "BIND": (["core", "socket", "numa", "none"], "core"),
        "UNCORE_MAX_MHZ": ([2500, 2000, 1600], 2500),
        "EPB": ([6, 4, 0], 6),
        "THP": (["madvise", "always", "never"], "madvise"),
        "HOURS": ([1, 2, 3, 6], 3),
    }

    MAX_CPUS = 128
    UNCORE_SYSFS = autotune_local.UNCORE_SYSFS
    THP_ENABLED = autotune_local.THP_ENABLED
    EPB_SYSFS = autotune_local.EPB_SYSFS

    def __init__(self, tunable: list[str] | None = None, fixed: dict[str, Any] | None = None) -> None:
        tunable = list(tunable or self.PARAMETERS)
        fixed = dict(fixed or {})
        unknown = [n for n in tunable + list(fixed) if n not in self.PARAMETERS]
        if unknown:
            raise ValueError(f"Unknown WRF parameter(s): {unknown}")
        self.parameters = []
        for name, (choices, default) in self.PARAMETERS.items():
            if name in tunable:
                self.parameters.append(Parameter(name, "categorical", choices=choices))
            else:
                self.parameters.append(Parameter(name, "categorical", choices=choices,
                                                 fixed_value=fixed.get(name, default)))

    def resolve_configuration(self, configuration: dict[str, Any]) -> dict[str, Any]:
        resolved = dict(configuration)
        for p in self.parameters:
            if p.fixed_value is not None:
                resolved.setdefault(p.name, p.fixed_value)
        resolved["total_threads"] = int(resolved["RANKS"]) * int(resolved["THREADS"])
        return resolved

    def forbidden_clauses(self, cs: Any) -> list[Any]:
        """RANKS x THREADS > 128, and THREADS > 1 on the MPI-only build."""
        from ConfigSpace import ForbiddenAndConjunction, ForbiddenEqualsClause, ForbiddenInClause

        names = set(cs.keys())
        clauses = []
        threads = self.PARAMETERS["THREADS"][0]
        if {"RANKS", "THREADS"} <= names:
            for r in self.PARAMETERS["RANKS"][0]:
                over = [t for t in threads if r * t > self.MAX_CPUS]
                if over:
                    clauses.append(ForbiddenAndConjunction(ForbiddenEqualsClause(cs["RANKS"], r),
                                                           ForbiddenInClause(cs["THREADS"], over)))
        if {"BUILD", "THREADS"} <= names:
            clauses.append(ForbiddenAndConjunction(ForbiddenEqualsClause(cs["BUILD"], "gnu"),
                                                   ForbiddenInClause(cs["THREADS"], [t for t in threads if t > 1])))
        return clauses

    def runwrf_args(self, c: dict[str, Any]) -> list[str]:
        return ["--exe", c["BUILD"], "--ranks", str(int(c["RANKS"])), "--threads", str(int(c["THREADS"])),
                "--tiles", str(int(c["TILES"])), "--decomp", c["DECOMP"], "--bind", c["BIND"],
                "--hours", str(int(c["HOURS"]))]

    def command(self, configuration: dict[str, Any]) -> str:
        c = configuration
        args = shlex.join(self.runwrf_args(c))
        return "\n".join([
            *autotune_local.cleanup_header(),
            *autotune_local.thp_lines(str(c["THP"]), self.THP_ENABLED),
            *autotune_local.uncore_lines(int(c["UNCORE_MAX_MHZ"]), self.UNCORE_SYSFS),
            *autotune_local.epb_lines(int(c["EPB"]), self.EPB_SYSFS),
            'RUN="${SLURM_SUBMIT_DIR:-$PWD}/outputs/wrf/tuning_runs/${SLURM_JOB_ID:-local}"',
            f'python3 {RUNWRF} --run-dir "$RUN" {args}',
        ])

    def parse_result(self, output: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for line in output.splitlines():
            if line.startswith(RESULT_TAG):
                try:
                    result = json.loads(line[len(RESULT_TAG):])
                except json.JSONDecodeError:
                    pass
        ok = bool(result.get("success")) and (result.get("speed") or 0) > 0
        metrics = {k: result[k] for k in ("speed", "mean_d01_step_s", "counted_wall_s", "total_wall_s",
                                          "decomp", "run_dir", "returncode") if k in result}
        return {"metrics": metrics, "objective": metrics.get("speed") if ok else None, "success": ok}

    @classmethod
    def from_config(cls, config: ExperimentConfig) -> "WRFApplication":
        return cls(tunable=config.tunable, fixed=config.fixed)


common.register_application("wrf_local", WRFApplication.from_config)


if __name__ == "__main__":
    sys.exit(autotune_local.main(description="SMAC3 autotuning of WRF on Launchpad"))
