#!/usr/bin/env python3
"""STREAM application for the Launchpad SMAC3 autotuner (see ../autotune_local.py).

Each evaluation compiles stream.c with icx for the chosen compile-time and
compiler settings, then runs it with the chosen OpenMP / NUMA placement. The
objective is Triad bandwidth (MB/s); a run only counts if STREAM prints
"Solution Validates".

MPI_RANKS > 1 instead builds the official MPI STREAM (stream_mpi.c, same
flags, Intel MPI's mpiicx) and launches it with Intel MPI's mpirun. The
arrays are split across ranks (each rank allocates and first-touches its own
share, so placement follows its pinning). OMP_THREADS is threads per rank;
MPI_RANKS x OMP_THREADS may not exceed the 128 logical CPUs (SMAC never
suggests such pairs). stream_mpi.c ignores OFFSET. MPI_RANKS = 1 is plain
stream.c, as before, so older runs stay valid.

The space mirrors streamsweep.py (the earlier random sweep) so its results
can seed SMAC as priors, minus settings that make the number meaningless or
are known dead ends:

* STREAM_TYPE is double and every array is >= 1 GiB (>= 4x the 2 x 120 MB L3,
  STREAM's own rule); smaller arrays measure cache, not memory, bandwidth.
* -mcmodel=medium is always on (static arrays > 2 GB do not link otherwise).
* Single-NUMA-node policies (membind_N / cpu_mem_node_N) and fewer than 16
  threads are left out: they cap bandwidth at one socket / a few cores.

Usage (inside the launchpad env, see stream_smac3.sh):

    python scripts/Launchpad/stream/stream_autotune.py --config scripts/Launchpad/stream/stream_smac3.yaml
    python scripts/Launchpad/stream/stream_autotune.py --config ... --check-replay
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from hpc_autotuner.applications.base import Application
from hpc_autotuner.core.parameter import Parameter
from hpc_autotuner.experiments import common
from hpc_autotuner.experiments.config import ExperimentConfig

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autotune_local  # noqa: E402

KERNELS = ("Copy", "Scale", "Add", "Triad")


class StreamApplication(Application):
    """icx-compiled OpenMP STREAM, tuned on Triad MB/s."""

    objective_metric = "triad_mb_s"
    objective_direction = "maximize"

    #: name -> (choices, default). Values use streamsweep.py's labels.
    PARAMETERS: dict[str, tuple[list[Any], Any]] = {
        # STREAM_ARRAY_SIZE (elements per array; 8 bytes each)
        "ARRAY_SIZE": ([134_217_728, 268_435_456, 402_653_184, 536_870_912, 805_306_368,
                        1_073_741_824, 2_147_483_648, 4_294_967_296], 268_435_456),
        "NTIMES": ([10, 20, 30, 50, 100], 100),
        "OFFSET": ([0, 1, 8, 16, 32, 64], 0),
        "OPT": (["-O2", "-O3", "-Ofast"], "-O3"),
        "ISA": (["compiler_default", "host", "avx2", "avx512", "march_native", "march_mtune_native"], "avx512"),
        "STORES": (["streaming_auto", "streaming_always", "streaming_never"], "streaming_always"),
        "EXTRA": (["no_extra", "unroll_loops", "fast_math", "lto", "alias_analysis"], "no_extra"),
        # OpenMP threads per MPI rank. With one rank: 64 = one per physical
        # core, 128 = every hyperthread, finer steps around 64 where bandwidth
        # peaks. 1-8 are for MPI_RANKS > 1 (MPI_RANKS x threads <= 128).
        "OMP_THREADS": ([1, 2, 4, 8, 16, 24, 32, 40, 48, 56, 64, 72, 80, 96, 112, 128], 64),
        # Intel OpenMP: KMP_AFFINITY (unless "disabled") overrides
        # OMP_PROC_BIND/OMP_PLACES; all three are kept as in the sweep.
        "KMP_AFFINITY": (["granularity=fine,compact,1,0", "granularity=fine,scatter,1,0",
                          "granularity=core,compact,1,0", "granularity=fine,balanced", "disabled"],
                         "granularity=fine,compact,1,0"),
        "OMP_PROC_BIND": (["false", "true", "close", "spread"], "spread"),
        "OMP_PLACES": (["cores", "threads", "sockets"], "cores"),
        # How long idle OpenMP threads spin before sleeping (Intel OpenMP;
        # "default" = 200 ms). STREAM runs many short parallel loops, so the
        # wake-up cost between them can show up.
        "KMP_BLOCKTIME": (["default", "0", "infinite"], "default"),
        "NUMA": (["default", "interleave_all"], "default"),
        # MPI ranks (1 = OpenMP-only stream.c). 64 = one rank per core.
        "MPI_RANKS": ([1, 2, 4, 8, 16, 32, 64], 1),
        # Intel MPI pinning (MPI_RANKS > 1): I_MPI_PIN_DOMAIN "omp" = one
        # domain of OMP_NUM_THREADS logical CPUs per rank, "auto" = CPUs split
        # evenly over ranks, "numa" = one NUMA node per domain (ranks share).
        # I_MPI_PIN_ORDER places consecutive ranks next to each other
        # ("compact") or across sockets ("scatter").
        "MPI_PIN_DOMAIN": (["omp", "auto", "numa"], "omp"),
        "MPI_PIN_ORDER": (["compact", "scatter"], "compact"),
        # Machine-wide, set per job and restored after (sudo):
        # uncore (mesh/LLC/memory controller) frequency cap; 2500 = default.
        "UNCORE_MAX_MHZ": ([2500, 2000, 1600, 1400, 1200, 1000, 800], 2500),
        # Transparent huge pages. STREAM's arrays are static (.bss), so only
        # the system-wide "always" mode backs them with 2 MB pages; "madvise"
        # (this node's default) and "never" leave them on 4 KB pages.
        "THP": (["madvise", "always", "never"], "madvise"),
        # Pin the uncore min to UNCORE_MAX_MHZ, so it never clocks down
        # ("off" = dynamic, 800 MHz floor).
        "UNCORE_PIN": (["off", "on"], "off"),
        # Energy-performance bias on every CPU (6 = this node's default,
        # 4 balance-performance, 0 performance); firmware also uses it for
        # uncore and memory power management.
        "EPB": ([6, 4, 0], 6),
        # Idle states disabled for the run ("all" = all enabled).
        "CSTATES": (["all", "no_c6", "c1_only"], "all"),
    }

    #: First time the 2.0 GHz uncore cap was seen set on this node; earlier
    #: STREAM runs ran at the 2.5 GHz default (used when importing old runs).
    UNCORE_2000_SINCE = "2026-10-17T21:46"

    _ISA_FLAGS = {
        "compiler_default": [],
        "host": ["-xHost"],
        "avx2": ["-xCORE-AVX2"],
        "avx512": ["-xCORE-AVX512", "-qopt-zmm-usage=high"],
        "march_native": ["-march=native"],
        "march_mtune_native": ["-march=native", "-mtune=native"],
    }
    _STORE_FLAGS = {
        "streaming_auto": ["-qopt-streaming-stores=auto"],
        "streaming_always": ["-qopt-streaming-stores=always"],
        "streaming_never": ["-qopt-streaming-stores=never"],
    }
    _EXTRA_FLAGS = {
        "no_extra": [],
        "unroll_loops": ["-funroll-loops"],
        "fast_math": ["-ffast-math"],
        "lto": ["-flto"],
        "alias_analysis": ["-fno-alias"],
    }
    _NUMA_PREFIX = {"default": "", "interleave_all": "numactl --interleave=all "}
    _CSTATE_DISABLE = {"all": [], "no_c6": ["C6"], "c1_only": ["C1E", "C6"]}

    #: Logical CPUs on the node; MPI_RANKS x threads per rank must fit.
    MAX_CPUS = 128

    #: Official MPI STREAM, relative to the repo root (stream_smac3.sh
    #: downloads it).
    MPI_SOURCE = "libs/clone/stream_mpi/stream_mpi.c"

    #: System paths the job touches (overridable for tests).
    UNCORE_SYSFS = autotune_local.UNCORE_SYSFS
    THP_ENABLED = autotune_local.THP_ENABLED
    EPB_SYSFS = autotune_local.EPB_SYSFS
    CPUIDLE_SYSFS = autotune_local.CPUIDLE_SYSFS

    def __init__(self, source: Path, compiler: str = "icx", mpi_source: Path | None = None,
                 mpi_compiler: str = "mpiicx",
                 tunable: list[str] | None = None, fixed: dict[str, Any] | None = None) -> None:
        self.source = source
        self.compiler = compiler
        self.mpi_source = mpi_source
        self.mpi_compiler = mpi_compiler
        tunable = list(tunable or self.PARAMETERS)
        fixed = dict(fixed or {})
        unknown = [n for n in tunable + list(fixed) if n not in self.PARAMETERS]
        if unknown:
            raise ValueError(f"Unknown STREAM parameter(s): {unknown}")
        if mpi_source is None and ("MPI_RANKS" in tunable or int(fixed.get("MPI_RANKS", 1)) > 1):
            raise ValueError("MPI_RANKS > 1 needs mpi_source (stream_mpi.c)")
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
        resolved["array_memory_gib"] = 3 * int(resolved["ARRAY_SIZE"]) * 8 / 1024**3
        ranks, threads = int(resolved["MPI_RANKS"]), int(resolved["OMP_THREADS"])
        assert ranks * threads <= self.MAX_CPUS, (
            f"{ranks} ranks x {threads} threads exceeds {self.MAX_CPUS} logical CPUs")
        resolved["total_threads"] = ranks * threads
        return resolved

    def forbidden_clauses(self, cs: Any) -> list[Any]:
        """MPI_RANKS x OMP_THREADS > MAX_CPUS, for SMAC never to suggest."""
        from ConfigSpace import ForbiddenAndConjunction, ForbiddenEqualsClause, ForbiddenInClause

        if not {"MPI_RANKS", "OMP_THREADS"} <= set(cs.keys()):
            return []  # one is fixed; resolve_configuration checks the pair
        clauses = []
        for ranks in self.PARAMETERS["MPI_RANKS"][0]:
            over = [t for t in self.PARAMETERS["OMP_THREADS"][0] if ranks * t > self.MAX_CPUS]
            if over:
                clauses.append(ForbiddenAndConjunction(ForbiddenEqualsClause(cs["MPI_RANKS"], ranks),
                                                       ForbiddenInClause(cs["OMP_THREADS"], over)))
        return clauses

    def compile_flags(self, c: dict[str, Any]) -> list[str]:
        return ([
            f"-DSTREAM_ARRAY_SIZE={int(c['ARRAY_SIZE'])}", f"-DNTIMES={int(c['NTIMES'])}",
            f"-DOFFSET={int(c['OFFSET'])}", "-DSTREAM_TYPE=double",
            c["OPT"], "-qopenmp", "-mcmodel=medium",
        ] + self._ISA_FLAGS[c["ISA"]] + self._STORE_FLAGS[c["STORES"]] + self._EXTRA_FLAGS[c["EXTRA"]])

    def command(self, configuration: dict[str, Any]) -> str:
        c = configuration
        ranks = int(c["MPI_RANKS"])
        compiler, source = (self.compiler, self.source) if ranks == 1 else (self.mpi_compiler, self.mpi_source)
        cc = shlex.join([compiler] + self.compile_flags(c) + [str(source), "-o", "$RUNDIR/stream.exe"])
        cc = cc.replace("'$RUNDIR/stream.exe'", '"$RUNDIR/stream.exe"')
        run = f'{self._NUMA_PREFIX[c["NUMA"]]}"$RUNDIR/stream.exe"'
        mpi = []
        if ranks > 1:
            run = f"mpirun -np {ranks} {run}"
            threads = int(c["OMP_THREADS"])
            mpi = [f"echo 'MPI: {ranks} ranks x {threads} threads = {ranks * threads} total'",
                   "export I_MPI_FABRICS=shm", "export I_MPI_PIN=1",
                   f"export I_MPI_PIN_DOMAIN={c['MPI_PIN_DOMAIN']}", f"export I_MPI_PIN_ORDER={c['MPI_PIN_ORDER']}"]
        return "\n".join([
            *autotune_local.cleanup_header(),
            'RUNDIR="${SLURM_SUBMIT_DIR:-$PWD}/outputs/stream_runs/${SLURM_JOB_ID:-local}"',
            'mkdir -p "$RUNDIR"',
            f"echo {shlex.quote('compile: ' + cc)}",
            cc,
            f"export OMP_NUM_THREADS={int(c['OMP_THREADS'])}",
            f"export KMP_AFFINITY={c['KMP_AFFINITY']}",
            f"export OMP_PROC_BIND={c['OMP_PROC_BIND']}",
            f"export OMP_PLACES={c['OMP_PLACES']}",
            "export OMP_DYNAMIC=FALSE",
            ("unset KMP_BLOCKTIME" if c["KMP_BLOCKTIME"] == "default" else f"export KMP_BLOCKTIME={c['KMP_BLOCKTIME']}"),
            *autotune_local.thp_lines(str(c["THP"]), self.THP_ENABLED),
            *mpi,
            *autotune_local.uncore_lines(int(c["UNCORE_MAX_MHZ"]), self.UNCORE_SYSFS, pin=c["UNCORE_PIN"] == "on"),
            *autotune_local.epb_lines(int(c["EPB"]), self.EPB_SYSFS),
            *autotune_local.cstate_lines(self._CSTATE_DISABLE[c["CSTATES"]], self.CPUIDLE_SYSFS),
            run,
            'rm -f "$RUNDIR/stream.exe"',
            'rmdir "$RUNDIR" 2>/dev/null || true',
        ])

    def parse_result(self, output: str) -> dict[str, Any]:
        metrics: dict[str, Any] = {"validates": "Solution Validates" in output}
        for kernel in KERNELS:
            m = re.search(rf"(?m)^\s*{kernel}:\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)",
                          output)
            if m:
                metrics[f"{kernel.lower()}_mb_s"] = float(m.group(1))
        ok = metrics["validates"] and all(f"{k.lower()}_mb_s" in metrics for k in KERNELS)
        objective = metrics.get("triad_mb_s") if ok else None
        return {"metrics": metrics, "objective": objective, "success": ok}

    # -- priors -------------------------------------------------------------

    def prior_records(self, path: Path) -> list[dict[str, Any]]:
        """Seed records from either a run group's evaluations.jsonl (earlier
        SMAC runs; knobs they predate are filled with what they ran with) or
        the streamsweep.py trials file."""
        first = next((json.loads(l) for l in path.read_text().splitlines() if l.strip()), {})
        if "configuration" in first:
            return self._prior_evaluations(path)
        return self._prior_sweep(path)

    def _prior_evaluations(self, path: Path) -> list[dict[str, Any]]:
        def fill(record: dict[str, Any], config: dict[str, Any]) -> None:
            # Knobs older runs predate, at what they actually ran with.
            since_cap = (record.get("submitted_at") or "") >= self.UNCORE_2000_SINCE
            config.setdefault("UNCORE_MAX_MHZ", 2000 if since_cap else 2500)
            config.setdefault("THP", "madvise")
            config.setdefault("KMP_BLOCKTIME", "default")
            # stream_smac3_v3's MPI runs logged OMP_THREADS as the total over
            # ranks, with the per-rank count in threads_per_rank.
            if "threads_per_rank" in config:
                config["OMP_THREADS"] = config.pop("threads_per_rank")
            config.setdefault("MPI_RANKS", 1)
            config.setdefault("UNCORE_PIN", "off")
            config.setdefault("EPB", 6)
            config.setdefault("CSTATES", "all")
            config.setdefault("MPI_PIN_DOMAIN", "omp")
            config.setdefault("MPI_PIN_ORDER", "compact")

        return autotune_local.import_evaluations(self, path, fill)

    def _prior_sweep(self, path: Path) -> list[dict[str, Any]]:
        """Turn streamsweep.py trials that fall inside this space into
        evaluation records (same source file and compiler; finished runs only
        -- the one in-space compile failure has no recorded cause)."""
        source_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        names = {"ARRAY_SIZE": "array_size", "NTIMES": "ntimes", "OFFSET": "offset",
                 "OPT": "optimization", "ISA": "isa", "STORES": "streaming_stores",
                 "EXTRA": "extra_optimization", "OMP_THREADS": "omp_threads",
                 "KMP_AFFINITY": "kmp_affinity", "OMP_PROC_BIND": "omp_proc_bind",
                 "OMP_PLACES": "omp_places", "NUMA": "numa_mode"}
        status_map = {"ok": "COMPLETED", "run_timeout": "TIMEOUT", "run_failed": "FAILED",
                      "invalid_or_unrecognized_output": "FAILED"}
        tunable = {p.name for p in self.tunable_parameters}
        fixed = {p.name: p.fixed_value for p in self.parameters if p.fixed_value is not None}
        records = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            t = json.loads(line)
            if (t.get("status") not in status_map or t.get("compiler_family") != "intel"
                    or t.get("source_sha256") != source_sha or t.get("stream_type") != "double"
                    or t.get("memory_model") != "medium_model"):
                continue
            cfg = {name: t.get(key) for name, key in names.items()}
            # The sweep ran (Oct 11-12) at this node's defaults.
            cfg.update({"UNCORE_MAX_MHZ": 2500, "THP": "madvise", "KMP_BLOCKTIME": "default",
                    "MPI_RANKS": 1, "MPI_PIN_DOMAIN": "omp", "MPI_PIN_ORDER": "compact",
                    "UNCORE_PIN": "off", "EPB": 6, "CSTATES": "all"})
            if any(cfg[n] not in self.PARAMETERS[n][0] for n in cfg):
                continue
            if any(cfg[n] != v for n, v in fixed.items()):
                continue
            parsed = {"metrics": dict(t.get("metrics") or {}), "objective": None, "success": False}
            if t["status"] == "ok":
                m = parsed["metrics"]
                parsed = {"metrics": m, "objective": m.get("triad_mb_s"), "success": m.get("triad_mb_s") is not None}
            records.append({
                "optimizer": "streamsweep",
                "configuration": self.resolve_configuration({n: cfg[n] for n in tunable | set(fixed)}),
                "job_id": None,
                "status": status_map[t["status"]] if parsed["success"] or t["status"] != "ok" else "FAILED",
                "success": parsed["success"],
                "objective": parsed["objective"],
                "metrics": parsed["metrics"],
                "triad_mb_s": parsed["objective"],
                "submitted_at": t.get("timestamp_utc"),
                "metadata": {"imported_from": str(path), "prior_trial": t.get("trial")},
            })
        return records

    @classmethod
    def from_config(cls, config: ExperimentConfig) -> "StreamApplication":
        root = config.resolved_project_root()
        source = Path(os.path.expandvars(config.executable or "stream.c"))
        return cls(source=(root / source).resolve(), mpi_source=(root / cls.MPI_SOURCE).resolve(),
                   tunable=config.tunable, fixed=config.fixed)


common.register_application("stream_local", StreamApplication.from_config)


if __name__ == "__main__":
    sys.exit(autotune_local.main(description="SMAC3 autotuning of STREAM on Launchpad"))
