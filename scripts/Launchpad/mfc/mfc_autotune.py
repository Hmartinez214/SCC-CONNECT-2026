#!/usr/bin/env python3
"""MFC application for the Launchpad SMAC3 autotuner (see ../autotune_local.py).

Each evaluation runs one case through runmfc.sh with the chosen build and
launch settings (building that variant first if it is not built yet). The
objective is simulation throughput, 1 / grind time, in G gp*eq*rhs per second
(MFC's "grind" is ns per grid point per equation per RHS evaluation; lower is
better). It is a maximized metric so that failed runs, which the tuner scores
as 0, rank last. The grind time itself is kept in the metrics as grind_ns.

Space (YAML ``tunable`` picks the searched ones; the rest use ``fixed`` or
the default):

* CASE_OPT   off|on      --case-optimization (case parameters compiled in)
* FASTMATH   off|on      -gpu=fastmath
* PRECISION  double|mixed|single   (fixed to double by default: the tuner
             only times runs, it does not check answers; use mfccheck.py)
* RDMA_MPI   off|on      rdma_mpi = 'T' (GPU buffers handed straight to MPI)
* BIND       none|numa|cores       rank pinning (launchpad_bind.sh)
* RANKS      1|2         MPI ranks = GPUs used
* GBPP, STEPS            problem size / length passed to benchmark cases
                         (fixed: they change the problem being timed)
* IGR_ITER_SOLVER, NUM_IGR_ITERS   IGR elliptic solve (igr cases; "case" = as in
                         the case file). Changes the numerics: check with mfccheck.py.
* UCX_TLS, UCX_RNDV_THRESH, UCX_RNDV_SCHEME   HPC-X transport for the MPI
                         halo exchange ("default" = unset). See mfc_igr_extra.yaml.

Build variants are cached by MFC, so only the first run of each
CASE_OPT x FASTMATH x PRECISION combination pays for a compile (the time
limit in the YAML allows for it); mfcvariants.sh can build them all first.

Usage (inside the launchpad env, see mfc_smac3.sh):

    python scripts/Launchpad/mfc/mfc_autotune.py --config scripts/Launchpad/mfc/mfc_smac3.yaml
    python scripts/Launchpad/mfc/mfc_autotune.py --config ... --check-replay
    python scripts/Launchpad/mfc/mfc_autotune.py --config ... --case igr   # run group mfc_smac3_igr

With the 4 tunables (24 combinations) SMAC tries every one and then stops
with ConfigurationSpaceExhaustedException; that is the normal end of a run.
"""

from __future__ import annotations

import itertools
import json
import random
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
sys.path.insert(0, str(HERE))
import autotune_local  # noqa: E402
import mfclib  # noqa: E402

RUNMFC = "scripts/Launchpad/mfc/runmfc.sh"


class MFCApplication(Application):
    """MFC on the H100s, tuned on simulation throughput."""

    objective_metric = "throughput"
    objective_direction = "maximize"

    #: name -> (choices, default)
    PARAMETERS: dict[str, tuple[list[Any], Any]] = {
        "CASE_OPT": (["off", "on"], "on"),
        "FASTMATH": (["off", "on"], "off"),
        "PRECISION": (["double", "mixed", "single"], "double"),
        "RDMA_MPI": (["off", "on"], "off"),
        "BIND": (["none", "numa", "cores"], "none"),
        "RANKS": ([1, 2], 2),
        "GBPP": ([1, 2, 4, 8, 16, 24, 32, 48, 64], 16),
        "STEPS": ([10, 20, 50, 100, 200, 500], 50),
        # IGR cases only (igr = 'T'); "case" keeps the case file's value.
        # Fewer iterations / another solver change the answer: check with mfccheck.py.
        "IGR_ITER_SOLVER": (["case", "jacobi", "gauss_seidel"], "case"),
        "NUM_IGR_ITERS": (["case", "1", "2", "3", "5", "8"], "case"),
        # HPC-X UCX transport (environment of every rank); "default" leaves it unset.
        "UCX_TLS": (["default", "sm_cuda_ipc", "sm_no_ipc"], "default"),
        "UCX_RNDV_THRESH": (["default", "8192", "65536", "1048576"], "default"),
        "UCX_RNDV_SCHEME": (["default", "put_zcopy", "get_zcopy"], "default"),
    }

    _IGR_SOLVER = {"jacobi": 1, "gauss_seidel": 2}
    _UCX_TLS = {"sm_cuda_ipc": "sm,self,cuda_copy,cuda_ipc", "sm_no_ipc": "sm,self,cuda_copy"}

    def __init__(self, case: str, tunable: list[str] | None = None,
                 fixed: dict[str, Any] | None = None) -> None:
        self.case = case
        _, self.slug, self.case_fixed_args = mfclib.resolve_case(case)
        tunable = list(tunable or self.PARAMETERS)
        fixed = dict(fixed or {})
        unknown = [n for n in tunable + list(fixed) if n not in self.PARAMETERS]
        if unknown:
            raise ValueError(f"Unknown MFC parameter(s): {unknown}")
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
        resolved["case"] = self.slug
        return resolved

    def runmfc_args(self, c: dict[str, Any]) -> list[str]:
        args = ["-n", str(int(c["RANKS"])), "--precision", c["PRECISION"], "--bind", c["BIND"],
                "--gbpp", str(int(c["GBPP"])), "--steps", str(int(c["STEPS"]))]
        if c["CASE_OPT"] == "on":
            args.append("--case-opt")
        if c["FASTMATH"] == "on":
            args.append("--fastmath")
        if c["RDMA_MPI"] == "on":
            args.append("--rdma")
        if c.get("IGR_ITER_SOLVER", "case") != "case":
            args += ["--param", f"igr_iter_solver={self._IGR_SOLVER[c['IGR_ITER_SOLVER']]}"]
        if c.get("NUM_IGR_ITERS", "case") != "case":
            args += ["--param", f"num_igr_iters={int(c['NUM_IGR_ITERS'])}"]
        if c.get("UCX_TLS", "default") != "default":
            args += ["--env", f"UCX_TLS={self._UCX_TLS[c['UCX_TLS']]}"]
        for var in ("UCX_RNDV_THRESH", "UCX_RNDV_SCHEME"):
            if c.get(var, "default") != "default":
                args += ["--env", f"{var}={c[var]}"]
        return args

    def command(self, configuration: dict[str, Any]) -> str:
        args = shlex.join(self.runmfc_args(configuration) + [self.case])
        return "\n".join([
            'OUT="${SLURM_SUBMIT_DIR:-$PWD}/outputs/mfc_tuning_runs"',
            f'{RUNMFC} --out "$OUT" --label "${{SLURM_JOB_ID:-local}}" {args}',
        ])

    def parse_result(self, output: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for line in output.splitlines():
            if line.startswith(mfclib.RESULT_TAG):
                try:
                    result = json.loads(line[len(mfclib.RESULT_TAG):])
                except json.JSONDecodeError:
                    pass
        return self._parsed(result)

    @staticmethod
    def _parsed(result: dict[str, Any]) -> dict[str, Any]:
        grind = result.get("grind_ns")
        ok = result.get("status") == "ok" and grind is not None and grind > 0
        metrics = {k: result.get(k) for k in ("grind_ns", "sim_exec_s", "pre_exec_s", "run_dir", "status")
                   if result.get(k) is not None}
        if ok:
            metrics["throughput"] = 1.0 / grind
        return {"metrics": metrics, "objective": metrics.get("throughput"), "success": ok}

    # -- priors -------------------------------------------------------------

    def prior_records(self, path: Path) -> list[dict[str, Any]]:
        """runmfc.sh runs (result.json under ``path``) of the same case and
        problem whose settings fall inside this space -- e.g. what
        mfcvariants.sh --run timed."""
        tunable = {p.name for p in self.tunable_parameters}
        fixed = {p.name: p.fixed_value for p in self.parameters if p.fixed_value is not None}
        records = []
        for r in mfclib.load_results(path):
            if r.get("case") != self.slug or r.get("mpirun_args"):
                continue
            # rdma_mpi, the IGR solver settings and the step count of `steps: param`
            # cases are part of the space; so are the UCX variables.
            overrides = r.get("overrides") or {}
            env = r.get("env") or {}
            if set(overrides) - {"rdma_mpi", "igr_iter_solver", "num_igr_iters",
                                 "t_step_start", "t_step_stop", "t_step_save"}:
                continue
            if set(env) - {"UCX_TLS", "UCX_RNDV_THRESH", "UCX_RNDV_SCHEME"}:
                continue
            args = list(r.get("case_args") or [])
            try:
                gbpp = int(args[args.index("--gbpp") + 1])
                steps = int(args[args.index("--steps") + 1])
            except (ValueError, IndexError):
                continue
            if args != self.case_fixed_args + ["--gbpp", str(gbpp), "--steps", str(steps)]:
                continue
            cfg = {
                "CASE_OPT": "on" if r.get("case_opt") else "off",
                "FASTMATH": "on" if r.get("fastmath") else "off",
                "PRECISION": r.get("precision"),
                "RDMA_MPI": "on" if r.get("rdma_mpi") else "off",
                "BIND": r.get("bind"),
                "RANKS": r.get("ranks"),
                "GBPP": gbpp,
                "STEPS": steps,
                "IGR_ITER_SOLVER": {v: k for k, v in self._IGR_SOLVER.items()}.get(
                    overrides.get("igr_iter_solver"), "case"),
                "NUM_IGR_ITERS": str(overrides["num_igr_iters"]) if "num_igr_iters" in overrides else "case",
                "UCX_TLS": {v: k for k, v in self._UCX_TLS.items()}.get(env.get("UCX_TLS"), "default"),
                "UCX_RNDV_THRESH": env.get("UCX_RNDV_THRESH", "default"),
                "UCX_RNDV_SCHEME": env.get("UCX_RNDV_SCHEME", "default"),
            }
            if any(cfg[n] not in self.PARAMETERS[n][0] for n in cfg):
                continue
            if any(cfg[n] != v for n, v in fixed.items()):
                continue
            parsed = self._parsed(r)
            records.append({
                "optimizer": "runmfc",
                "configuration": self.resolve_configuration({n: cfg[n] for n in tunable | set(fixed)}),
                "job_id": None,
                "status": "COMPLETED" if parsed["success"] else "FAILED",
                "success": parsed["success"],
                "objective": parsed["objective"],
                "metrics": parsed["metrics"],
                "throughput": parsed["objective"],
                "submitted_at": r.get("timestamp_utc"),
                "metadata": {"imported_from": r.get("run_dir")},
            })
        return records

    #: Set by --case; overrides the YAML's application.executable.
    case_override: str | None = None

    @classmethod
    def from_config(cls, config: ExperimentConfig) -> "MFCApplication":
        case = cls.case_override or config.executable or "5eq_rk3_weno3_hllc"
        return cls(case=case, tunable=config.tunable, fixed=config.fixed)


common.register_application("mfc_local", MFCApplication.from_config)


def _take_case(argv: list[str]) -> list[str]:
    """Handle --case CASE: tune that case instead of the YAML's, in run group
    <yaml run_group>_<slug> unless --run-group is given."""
    if "--case" not in argv:
        return argv
    i = argv.index("--case")
    case = argv[i + 1]
    argv = argv[:i] + argv[i + 2:]
    MFCApplication.case_override = case
    if "--run-group" not in argv:
        import yaml  # noqa: PLC0415

        config = argv[argv.index("--config") + 1]
        base = (yaml.safe_load(Path(config).read_text()).get("experiment") or {}).get("run_group", "mfc_smac3")
        argv += ["--run-group", f"{base}_{mfclib.resolve_case(case)[1]}"]
    return argv


class GridOptimizer:
    """Exhaustive search: every combination of the tunable parameters exactly
    once, in a random order fixed by the seed (so a restart replays the log in
    the same order and carries on). Used when the YAML says
    ``experiment.optimizer: grid``; budget should be the grid size."""

    def __init__(self, parameters: list[Parameter], seed: int | None = None, **_: Any) -> None:
        names = [p.name for p in parameters]
        grid = [dict(zip(names, values)) for values in itertools.product(*(p.choices for p in parameters))]
        random.Random(seed).shuffle(grid)
        self.grid = grid
        self.next = 0
        self.replay: list[dict[str, Any]] = []  # only reported by autotune_local.main

    def set_replay(self, configurations: list[dict[str, Any]]) -> None:
        self.replay = list(configurations)

    def suggest(self) -> dict[str, Any]:
        if self.next >= len(self.grid):
            raise StopIteration("grid exhausted: every combination has run")
        config = dict(self.grid[self.next])
        self.next += 1
        return config

    def observe(self, configuration: dict[str, Any], result: Any) -> None:
        pass


def _use_grid(argv: list[str]) -> None:
    """Swap the shared runtime's SMAC optimizer for GridOptimizer when the
    YAML asks for it (and record "grid" as the optimizer)."""
    import yaml  # noqa: PLC0415

    config = argv[argv.index("--config") + 1]
    if (yaml.safe_load(Path(config).read_text()).get("experiment") or {}).get("optimizer") != "grid":
        return
    autotune_local.ResumableSMAC3Optimizer = GridOptimizer
    run = common.run_experiment

    def run_grid(**kwargs: Any) -> Any:
        kwargs["optimizer_name"] = "grid"
        return run(**kwargs)

    autotune_local.common.run_experiment = run_grid


if __name__ == "__main__":
    argv = _take_case(sys.argv[1:])
    _use_grid(argv)
    sys.exit(autotune_local.main(argv, description="Autotuning of MFC on Launchpad"))
