#!/usr/bin/env python3
"""HPL applications for the Launchpad SMAC3 autotuner (see ../autotune_local.py).

``IntelHPLApplication`` (type ``hpl_intel``, hpl_smac3.yaml) runs Intel's
Distribution for LINPACK from MKL. ``LocalHPLApplication`` (type ``hpl_local``,
hpl_netlib_smac3.yaml) launches spack's netlib xhpl with Intel MPI's ``mpirun`` instead of
``srun``, adds MPI rank-count / binding / NUMA / huge-page launch knobs to the
search space, and only counts a run as successful if HPL's residual check
PASSED.

Usage (inside the launchpad env, see hpl_smac3.sh):

    python scripts/Launchpad/hpl/hpl_autotune.py --config scripts/Launchpad/hpl/hpl_smac3.yaml
    python scripts/Launchpad/hpl/hpl_autotune.py --config ... --check-replay
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from hpc_autotuner.applications.hpl import HPLApplication
from hpc_autotuner.core.parameter import Parameter
from hpc_autotuner.experiments import common
from hpc_autotuner.experiments.config import ExperimentConfig

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import autotune_local  # noqa: E402


def _cpu_topology() -> list[list[list[int]]]:
    """sockets -> physical cores -> logical CPUs (HW threads), from sysfs."""
    sockets: dict[int, dict[int, list[int]]] = {}
    for cpu_dir in Path("/sys/devices/system/cpu").glob("cpu[0-9]*"):
        topo = cpu_dir / "topology"
        if not topo.is_dir():
            continue
        cpu = int(cpu_dir.name[3:])
        pkg = int((topo / "physical_package_id").read_text())
        core = int((topo / "core_id").read_text())
        sockets.setdefault(pkg, {}).setdefault(core, []).append(cpu)
    return [
        sorted((sorted(cpus) for cpus in cores.values()), key=lambda cpus: cpus[0])
        for _, cores in sorted(sockets.items())
    ]


def _numa_cores() -> list[list[int]]:
    """NUMA nodes -> first HW thread of each physical core in the node, from
    sysfs. Equals the socket layout with sub-NUMA clustering off; with SNC on,
    each socket splits into several nodes."""
    first = {threads[0] for cores in _cpu_topology() for threads in cores}
    nodes = []
    for node in sorted(Path("/sys/devices/system/node").glob("node[0-9]*"), key=lambda d: int(d.name[4:])):
        cpus = []
        for part in (node / "cpulist").read_text().strip().split(","):
            if part:
                lo, _, hi = part.partition("-")
                cpus += range(int(lo), int(hi or lo) + 1)
        cores = sorted(c for c in cpus if c in first)
        if cores:
            nodes.append(cores)
    return nodes


def _mask(cpus: list[int]) -> str:
    """Intel MPI pin-domain mask for a set of logical CPUs."""
    return hex(sum(1 << cpu for cpu in cpus))


def _cpulist(cpus: list[int]) -> str:
    """Sorted CPU ids as a list with ranges ("0-15,64-79")."""
    out, start = [], None
    for k, cpu in enumerate(cpus):
        if start is None:
            start = cpu
        if k + 1 == len(cpus) or cpus[k + 1] != cpu + 1:
            out.append(f"{start}-{cpu}" if cpu != start else f"{cpu}")
            start = None
    return ",".join(out)


class LocalHPLApplication(HPLApplication):
    """HPL launched with Intel MPI's mpirun, plus MPI binding/NUMA knobs.

    The launch parameters below sit alongside the HPL.dat ones: list a name
    under ``application.tunable`` to let the optimizer vary it, or pin it
    under ``application.fixed``. The defaults reproduce the original launch
    (I_MPI_PIN_PROCESSOR_LIST=allcores: rank r on the first HW thread of
    core r, no memory policy, no huge pages).

    Placement is spelled out as explicit Intel MPI CPU lists / domain masks
    rather than its named presets: on this Intel MPI (2021.18) the
    ``all``/``allsocks``/``map=``/``grain=`` processor lists fail to parse and
    silently fall back to the default, and I_MPI_PIN_ORDER barely changes the
    result, so the presets mostly collapse onto the same placement.
    """

    mpirun = "mpirun"

    #: name -> (choices, default)
    LAUNCH_PARAMETERS: dict[str, tuple[list[Any], Any]] = {
        # MPI rank count: one per physical core (64) or one per HW thread
        # (128, i.e. using hyper-threading; HPL here is MPI-only with
        # sequential MKL, so extra ranks are the only way to use the
        # siblings). P is chosen from the divisors of slurm.ntasks (64), which
        # all divide 128 too, and Q = RANKS / P.
        "RANKS": ([64, 128], 64),
        # What each rank is bound to:
        #   none     I_MPI_PIN=0, the Linux scheduler places ranks
        #   hwthread one logical CPU (with 64 ranks the first HW thread of
        #            each core, with 128 every HW thread)
        #   core     one physical core (both of its HW threads; with 128
        #            ranks two ranks share each core's domain)
        #   socket   any CPU of one socket (= one NUMA node here)
        #   node     any CPU of the machine (pinned to the node, floats)
        "MPI_BIND": (["none", "hwthread", "core", "socket", "node"], "hwthread"),
        # Rank -> socket layout, which decides how HPL's P x Q grid lands on
        # the sockets: block fills socket 0 then socket 1, cyclic alternates.
        # Ignored for none/node.
        "MPI_MAP": (["block", "cyclic"], "block"),
        # numactl memory policy for each rank. (--localalloc is omitted: it
        # is the same as Linux's default policy.)
        "MEM_POLICY": (["default", "interleave"], "default"),
        # THP is "madvise" on this node, so the matrix only gets huge pages if
        # malloc asks: glibc.malloc.hugetlb=1 makes it madvise them.
        "HUGEPAGES": (["off", "thp"], "off"),
    }

    _MEM_POLICY_PREFIX = {
        "default": "",
        "interleave": "numactl --interleave=all ",
    }

    @classmethod
    def for_benchmark(cls, *, tunable: list[str] | None = None, fixed: dict[str, Any] | None = None,
                      **kwargs: Any) -> "LocalHPLApplication":
        tunable = list(tunable or [])
        fixed = dict(fixed or {})
        launch_fixed = {k: fixed.pop(k) for k in list(fixed) if k in cls.LAUNCH_PARAMETERS}
        app = super().for_benchmark(
            tunable=[t for t in tunable if t not in cls.LAUNCH_PARAMETERS] or None,
            fixed=fixed,
            **kwargs,
        )
        for name, (choices, default) in cls.LAUNCH_PARAMETERS.items():
            if name in tunable:
                app.parameters.append(Parameter(name, "categorical", choices=choices))
            else:
                value = launch_fixed.get(name, default)
                if value not in choices:
                    raise ValueError(f"{name}={value!r} is not one of {choices}")
                app.parameters.append(Parameter(name, "categorical", choices=choices, fixed_value=value))
        return app

    def _launch_values(self, configuration: dict[str, Any]) -> dict[str, Any]:
        """This run's launch knobs, defaults filled in."""
        return {name: configuration.get(name, default) for name, (_, default) in self.LAUNCH_PARAMETERS.items()}

    def _rank_slots(self, mapping: str, ranks: int) -> list[tuple[int, list[int], int]]:
        """(socket, HW threads of the core, the rank's own HW thread) for each
        rank, in rank order. Ranks fill physical cores first; with more ranks
        than cores, consecutive ranks share a core on its sibling threads."""
        topo = _cpu_topology()
        n_cores = sum(len(cores) for cores in topo)
        per_core = -(-ranks // n_cores)
        if any(len(core) < per_core for cores in topo for core in cores):
            raise ValueError(f"{ranks} ranks need {per_core} HW threads per core")
        per_socket = [
            [(s, core, core[t]) for core in cores for t in range(per_core)]
            for s, cores in enumerate(topo)
        ]
        if mapping == "block":
            order = [slot for slots in per_socket for slot in slots]
        else:
            order = [
                per_socket[s][i]
                for i in range(max(len(slots) for slots in per_socket))
                for s in range(len(per_socket)) if i < len(per_socket[s])
            ]
        return order[:ranks]

    def resolve_configuration(self, configuration: dict[str, Any]) -> dict[str, Any]:
        resolved = super().resolve_configuration(configuration)
        ranks = int(resolved["RANKS"])
        p = int(resolved["P"])
        if ranks % p:
            raise ValueError(f"P={p} does not divide RANKS={ranks}")
        resolved["Q"] = ranks // p
        return resolved

    def launch_lines(self, configuration: dict[str, Any]) -> list[str]:
        """Shell lines that apply the launch knobs and start xhpl."""
        c = self._launch_values(configuration)
        lines = [
            # Start from a clean slate so nothing inherited overrides a knob.
            "for v in $(compgen -e | grep '^I_MPI_PIN'); do unset \"$v\"; done",
            # Prints the actual rank -> CPU pinning map into the run log.
            "export I_MPI_DEBUG=4",
        ]
        bind = c["MPI_BIND"]
        n_ranks = int(c["RANKS"])
        launcher = ""
        if bind in ("core", "socket") and n_ranks > 64:
            # Intel MPI's launcher overflows a buffer ("stack smashing") on a
            # 128-entry I_MPI_PIN_DOMAIN mask list, and its named domains hand
            # domains out cyclically, which cannot express these layouts. So
            # bind each rank to the same CPU set itself, by local rank id.
            topo = _cpu_topology()
            if bind == "core":
                sets = [threads for _, threads, _ in self._rank_slots(c["MPI_MAP"], n_ranks)]
            else:
                sockets = [[cpu for core in cores for cpu in core] for cores in topo]
                sets = [sockets[s] for s, _, _ in self._rank_slots(c["MPI_MAP"], n_ranks)]
            table = " ".join(",".join(map(str, cpus)) for cpus in sets)
            lines += [
                "export I_MPI_PIN=0",
                "cat > \"$RUNDIR/bind_rank.sh\" <<'BIND'",
                "#!/bin/bash",
                f"cpus=({table})",
                'mine="${cpus[$MPI_LOCALRANKID]}"',
                'echo "bind_rank: rank $MPI_LOCALRANKID -> cpus {$mine}"',
                'exec numactl --physcpubind="$mine" "$@"',
                "BIND",
                "chmod +x \"$RUNDIR/bind_rank.sh\"",
            ]
            launcher = "\"$RUNDIR/bind_rank.sh\" "
        elif bind == "none":
            lines.append("export I_MPI_PIN=0")
        else:
            lines.append("export I_MPI_PIN=1")
            ranks = self._rank_slots(c["MPI_MAP"], n_ranks)
            if bind == "hwthread":
                cpus = ",".join(str(cpu) for _, _, cpu in ranks)
                lines.append(f"export I_MPI_PIN_PROCESSOR_LIST={cpus}")
            elif bind == "core":
                masks = ",".join(_mask(threads) for _, threads, _ in ranks)
                lines.append(f"export I_MPI_PIN_DOMAIN=[{masks}]")
            elif bind == "socket":
                topo = _cpu_topology()
                socket_masks = [_mask([cpu for core in cores for cpu in core]) for cores in topo]
                masks = ",".join(socket_masks[s] for s, _, _ in ranks)
                lines.append(f"export I_MPI_PIN_DOMAIN=[{masks}]")
            else:
                lines.append("export I_MPI_PIN_DOMAIN=node")
        if c["HUGEPAGES"] == "thp":
            lines.append("export GLIBC_TUNABLES=glibc.malloc.hugetlb=1")
        else:
            lines.append("unset GLIBC_TUNABLES")
        prefix = self._MEM_POLICY_PREFIX[c["MEM_POLICY"]]
        lines.append(f"{self.mpirun} -np {n_ranks} {launcher}{prefix}{self.executable}")
        return lines

    def command(self, configuration: dict[str, Any]) -> str:
        text = super().command(configuration)
        # No srun here; Intel MPI's mpirun (from the spack HPL deps) instead.
        text = text.replace(f"srun {self.executable}", "\n".join(self.launch_lines(configuration)))
        # The upstream template symlinks HPL.dat into the shared /tmp, which
        # can collide with other users' files; HPL reads it from $RUNDIR anyway.
        text = "\n".join(line for line in text.split("\n") if "/tmp/HPL.dat" not in line)
        return text

    def prior_records(self, path: Path) -> list[dict[str, Any]]:
        """Finished runs of another run group (``experiment.priors``); a launch
        knob an old run predates gets its fixed value or default -- what that
        run actually used."""
        def fill(_record: dict[str, Any], config: dict[str, Any]) -> None:
            for name, (_, default) in self.LAUNCH_PARAMETERS.items():
                config.setdefault(name, self.fixed_values.get(name, default))

        return autotune_local.import_evaluations(self, path, fill)

    def parse_result(self, output: str) -> dict[str, Any]:
        parsed = super().parse_result(output)
        checks = re.findall(r"\.\.\.\.\.\.\s+(PASSED|FAILED)", output)
        passed = bool(checks) and all(c == "PASSED" for c in checks)
        parsed["metrics"]["residual_passed"] = passed
        if not passed:
            # A fast wrong answer is not a result.
            parsed["objective"] = None
            parsed["success"] = False
        return parsed


class IntelHPLApplication(LocalHPLApplication):
    """Intel's Distribution for LINPACK (``xhpl_intel64_dynamic`` from MKL's
    ``benchmarks/mp_linpack``), launched the way Intel's runme scripts do:
    each rank multithreads across the cores it owns. Same HPL.dat parameters
    and residual check as netlib.

    Launch knobs (replacing netlib's RANKS/P/MPI_BIND/MPI_MAP):

    * ``GRID`` (P x Q). Up to one rank per NUMA node uses ``HPL_HOST_NODE``
      (1x1 = one rank over every node); more ranks split each node's cores
      into equal contiguous blocks given via ``HPL_HOST_CORE`` (verified: each
      rank threads on exactly its block). Follows the real NUMA layout, so it
      stays correct with sub-NUMA clustering.
    * ``MEM_POLICY``: with GRID=1x1 the matrix lands almost entirely on NUMA
      node 0 (measured 50.6 of 50.9 GiB); "interleave" spreads it
      (numactl --interleave=all).
    * Intel HPL environment knobs (semantics from the binary's
      mkl_mplinpack_ginit; see hpl/README.md): ``HPL_HT`` (use hyperthreads;
      pin domains / HPL_HOST_CORE then include the siblings),
      ``HPL_NUMTHREADS`` (threads per rank: all, all-1/-2/-4, 3/4, 1/2),
      ``HPL_PAIR`` x ``HPL_UMODE`` (update thread grouping / tiling),
      ``HPL_SWAPWIDTH``, ``HPL_SWAPTHRESHOLD``, and ``HPL_LARGEPAGE``
      (-1 THP off, 0 THP, 1 hugetlbfs: the job reserves the 2 MB pages it
      needs, matrix + 5% + 8 GB, and releases them on exit). ``HPL_LOG`` is recorded, not tuned
      (2 = most informative). Left out on purpose: HPL_HOST_LARGEPAGE (same
      as HPL_LARGEPAGE without offload), HPL_NUMPFACTS / HPL_SORTMODE /
      HPL_MEM_AFFINITY (no effect), HPL_HBM / HPL_MIC_* / HPL_PFACT_INFO
      (not this hardware), MKL_ENABLE_INSTRUCTIONS (can only force older
      kernels).
    * ``UNCORE_MAX_MHZ``: machine-wide uncore frequency cap, set per job and
      restored after (``manage_uncore = False`` leaves the uncore alone, for
      callers that control it themselves, e.g. system_ab.sh).

    NB spans 256-2048 (Intel recommends 1536 on 4th-gen Xeon Scalable).
    """

    HPL_PARAMETER_DEFAULTS = {
        **HPLApplication.HPL_PARAMETER_DEFAULTS,
        "NB": ("int", (256, 2048), 1536),
    }

    LAUNCH_PARAMETERS: dict[str, tuple[list[Any], Any]] = {
        "GRID": (["1x1", "1x2", "2x1", "2x2", "1x4", "4x1", "2x4", "4x2"], "1x1"),
        "MEM_POLICY": (["default", "interleave"], "default"),
        # Intel HPL environment knobs (defaults = Intel's defaults, i.e. what
        # runs without them used). Semantics: hpl/README.md.
        "HPL_HT": ([0, 1], 0),                   # 1: also use the 2nd hyperthread of each core
        "HPL_NUMTHREADS": (["all", "-1", "-2", "-4", "3/4", "1/2"], "all"),  # threads per rank
        "HPL_PAIR": ([1, 2, 4, 8], 1),           # update threads per group
        "HPL_UMODE": ([1, 2], 2),                # update tiling (only matters with HPL_PAIR > 1)
        "HPL_SWAPWIDTH": ([64, 128, 192], 192),  # row-swap piece width (192 = max on SPR/EMR)
        "HPL_SWAPTHRESHOLD": ([0, 25, 50, 75, 100], 100),  # % of matrix left at which swap algorithm switches
        "HPL_LARGEPAGE": ([-1, 0, 1], 0),        # -1 THP off, 0 THP (madvise), 1 hugetlbfs (job reserves pages)
        "HPL_LOG": ([0, 1, 2], 2),
        # 2500 = hardware default (uncapped); 800 = hardware minimum.
        "UNCORE_MAX_MHZ": ([2500, 2000, 1600, 1400, 1200, 1000, 800], 2500),
    }

    def resolve_configuration(self, configuration: dict[str, Any]) -> dict[str, Any]:
        # Skip LocalHPLApplication's RANKS-based grid; the grid comes from GRID.
        resolved = HPLApplication.resolve_configuration(self, configuration)
        p, q = (int(v) for v in str(resolved["GRID"]).split("x"))
        resolved.update({"P": p, "Q": q, "RANKS": p * q})
        return resolved

    #: Set the uncore cap per job (False: leave it to the caller).
    manage_uncore = True
    #: System paths the job touches (overridable for tests).
    UNCORE_SYSFS = autotune_local.UNCORE_SYSFS
    HUGEPAGE_PATHS = {"nr_hugepages": autotune_local.NR_HUGEPAGES, "meminfo": autotune_local.MEMINFO,
                      "drop_caches": autotune_local.DROP_CACHES, "compact_memory": autotune_local.COMPACT_MEMORY}

    def launch_lines(self, configuration: dict[str, Any]) -> list[str]:
        c = self._launch_values(configuration)
        p, q = (int(v) for v in str(c["GRID"]).split("x"))
        ranks = p * q
        rank_cpus, placement = self._placement(ranks, int(c["HPL_HT"]) != 0, str(c["GRID"]))
        lines = [
            *autotune_local.cleanup_header(),
            "for v in $(compgen -e | grep '^I_MPI_PIN'); do unset \"$v\"; done",
            "export I_MPI_DEBUG=4",
            "export I_MPI_PIN=1",
            # The runner pins these to 1 for the MPI-only netlib build; Intel's
            # HPL must be free to thread across its cores.
            "unset OMP_NUM_THREADS MKL_NUM_THREADS",
            # Intel's HPL puts its memory on THP itself; no glibc hint.
            "unset GLIBC_TUNABLES",
            *placement,
            *self._env_lines(c, per_rank=len(rank_cpus[0])),
        ]
        if int(c["HPL_LARGEPAGE"]) == 1:
            # hugetlbfs pages for matrix + 5%, plus 8 GB: Intel's HPL also
            # puts its work buffers on them first (N=30k failed with 5% + 64).
            n = int(configuration["N"])
            lines += autotune_local.hugepage_lines(n * n * 8 // 2**21 * 105 // 100 + 4096, **self.HUGEPAGE_PATHS)
        if self.manage_uncore:
            lines += autotune_local.uncore_lines(int(c["UNCORE_MAX_MHZ"]), self.UNCORE_SYSFS)
        prefix = self._MEM_POLICY_PREFIX[c["MEM_POLICY"]]
        lines.append(f"{self.mpirun} -np {ranks} -perhost {ranks} \"$RUNDIR/intel_rank.sh\" {prefix}{self.executable}")
        return lines

    def _placement(self, ranks: int, ht: bool, grid: str) -> tuple[list[list[int]], list[str]]:
        """Each rank's logical CPUs, and the shell lines that pin it there.

        Up to one rank per NUMA node: Intel's ``HPL_HOST_NODE``. More ranks
        split every node's cores into equal blocks given via ``HPL_HOST_CORE``.
        With ``HPL_HT`` a rank also gets its cores' sibling hyperthreads.
        """
        numa = _numa_cores()                        # NUMA nodes -> physical cores (SNC-aware)
        siblings = {threads[0]: threads for cores in _cpu_topology() for threads in cores}

        def cpus_of(cores: list[int]) -> list[int]:
            return sorted(cpu for core in cores for cpu in (siblings[core] if ht else [core]))

        n_numa = len(numa)
        if ranks <= n_numa:
            if n_numa % ranks:
                raise ValueError(f"GRID {grid}: {n_numa} NUMA nodes do not split evenly over {ranks} ranks")
            per_rank = n_numa // ranks
            rank_cpus = [cpus_of(sum(numa[r * per_rank:(r + 1) * per_rank], [])) for r in range(ranks)]
            env = [f"export NUMA_PER_MPI={per_rank}"]
            pick = ["export HPL_HOST_NODE=$((r * NUMA_PER_MPI))-$(((r + 1) * NUMA_PER_MPI - 1))",
                    'echo "intel_rank: rank $r HPL_HOST_NODE=$HPL_HOST_NODE"']
        else:
            if ranks % n_numa:
                raise ValueError(f"GRID {grid}: {ranks} ranks do not split evenly over {n_numa} NUMA nodes")
            per_numa = ranks // n_numa
            rank_cpus = []
            for cores in numa:
                size = len(cores) // per_numa
                rank_cpus += [cpus_of(cores[i * size:(i + 1) * size]) for i in range(per_numa)]
            env = []
            pick = [f"cores=({' '.join(_cpulist(cpus) for cpus in rank_cpus)})",
                    "export HPL_HOST_CORE=${cores[$r]}",
                    'echo "intel_rank: rank $r HPL_HOST_CORE=$HPL_HOST_CORE"']
        lines = [
            # Each rank's MPI pin domain = the CPUs it owns.
            "export I_MPI_PIN_DOMAIN=[" + ",".join(_mask(cpus) for cpus in rank_cpus) + "]",
            *env,
            # Intel's runme_intel64_prv equivalent: tell each rank what it owns.
            "cat > \"$RUNDIR/intel_rank.sh\" <<'RANK'",
            "#!/bin/bash",
            "r=${MPI_LOCALRANKID:-${PMI_RANK:-0}}",
            *pick,
            'exec "$@"',
            "RANK",
            "chmod +x \"$RUNDIR/intel_rank.sh\"",
        ]
        return rank_cpus, lines

    @staticmethod
    def _env_lines(c: dict[str, Any], per_rank: int) -> list[str]:
        """Intel HPL's environment knobs; HPL_NUMTHREADS is relative to the
        ``per_rank`` logical CPUs each rank owns."""
        threads = {"all": None, "-1": per_rank - 1, "-2": per_rank - 2, "-4": per_rank - 4,
                   "3/4": per_rank * 3 // 4, "1/2": per_rank // 2}[str(c["HPL_NUMTHREADS"])]
        names = ("HPL_LOG", "HPL_LARGEPAGE", "HPL_HT", "HPL_PAIR", "HPL_UMODE", "HPL_SWAPWIDTH", "HPL_SWAPTHRESHOLD")
        return [
            "export " + " ".join(f"{k}={int(c[k])}" for k in names),
            f"export HPL_NUMTHREADS={max(threads, 1)}" if threads is not None else "unset HPL_NUMTHREADS",
        ]


def _factory(cls: type[LocalHPLApplication]):
    def build(config: ExperimentConfig) -> LocalHPLApplication:
        return cls.for_benchmark(
            executable=os.path.expandvars(config.executable) if config.executable else None,
            node_memory_bytes=config.node_memory_bytes,
            memory_factor=config.memory_factor,
            memory_fraction_bounds=config.memory_fraction_bounds,
            ntasks=config.slurm.ntasks,
            tunable=config.tunable,
            fixed=config.fixed,
        )
    return build


common.register_application("hpl_local", _factory(LocalHPLApplication))
common.register_application("hpl_intel", _factory(IntelHPLApplication))


# HPC-Tools checks P*Q against the fixed slurm.ntasks, but these applications
# tune the rank count. HPC-Tools has no hook for this, and a local change to
# the HPC-Tools submodule would be reset by setup_env.sh's
# `git submodule update --remote` on every runner start, so the check is
# replaced here instead.
def _validate_grid(config: dict[str, Any], slurm: Any) -> None:
    """The grid must match the evaluation's RANKS (slurm.ntasks if absent)."""
    ranks = int(config.get("RANKS", slurm.ntasks))
    if int(config["P"]) * int(config["Q"]) != ranks:
        raise ValueError(f"HPL grid {config['P']} x {config['Q']} does not match {ranks} ranks")


common._validate_grid = _validate_grid


if __name__ == "__main__":
    sys.exit(autotune_local.main(description="SMAC3 autotuning of HPL on Launchpad"))
