#!/usr/bin/env python3
"""HPL applications for the Launchpad SMAC3 autotuner (see ../autotune_local.py).

``IntelHPLApplication`` (type ``hpl_intel``, the default config) runs Intel's
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

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from hpc_autotuner.applications.hpl import HPLApplication
from hpc_autotuner.core.parameter import Parameter
from hpc_autotuner.core.space import ParameterSpace
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
        c = {name: configuration.get(name, default) for name, (_, default) in self.LAUNCH_PARAMETERS.items()}

        def mask(cpus: list[int]) -> str:
            return hex(sum(1 << cpu for cpu in cpus))

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
                masks = ",".join(mask(threads) for _, threads, _ in ranks)
                lines.append(f"export I_MPI_PIN_DOMAIN=[{masks}]")
            elif bind == "socket":
                topo = _cpu_topology()
                socket_masks = [mask([cpu for core in cores for cpu in core]) for cores in topo]
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
        """Finished runs from another run group's evaluations.jsonl, for
        seeding a new run group whose space added launch knobs: a knob the
        old record lacks gets its fixed value or default -- what the old run
        actually used. Used via ``experiment.priors`` (see autotune_local)."""
        final: dict[int, dict[str, Any]] = {}
        for line in path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("status") in common.TERMINAL_STATES and rec.get("attempt"):
                    final[int(rec["attempt"])] = rec
        space = ParameterSpace(self.tunable_parameters)
        records = []
        for attempt in sorted(final):
            rec = dict(final[attempt])
            config = dict(rec["configuration"])
            for name, (_, default) in self.LAUNCH_PARAMETERS.items():
                config.setdefault(name, self.fixed_values.get(name, default))
            config = self.resolve_configuration(config)
            space.validate(config)
            rec["configuration"] = config
            rec["job_id"] = None
            rec.setdefault("metadata", {})["imported_from"] = f"{path}#attempt={attempt}"
            records.append(rec)
        return records

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
    one MPI rank per NUMA node (or one for the whole machine), each rank
    multithreading across its node's cores and told which node it owns via
    ``HPL_HOST_NODE``. Same HPL.dat parameters and residual check as netlib.

    Differences from the netlib launch knobs:

    * ``GRID`` replaces RANKS/P/MPI_BIND/MPI_MAP: "1x2"/"2x1" = one rank per
      NUMA node (P x Q), "1x1" = a single rank spanning both nodes.
    * NB spans 256-2048 (Intel recommends 1536 on 4th-gen Xeon Scalable).
    * MEM_POLICY: with GRID=1x1 the single rank's matrix lands almost
      entirely on NUMA node 0 (measured: 50.6 of 50.9 GiB), so socket 1's
      threads work from remote memory; "interleave" spreads it across both
      nodes (numactl --interleave=all). Intel's HPL still binds the few
      regions it places explicitly.

    The binary also reads undocumented HPL_* variables (HPL_HOST_CORE,
    HPL_LARGEPAGE, HPL_HT, HPL_NUMTHREADS, HPL_SWAPWIDTH, ...); they are left
    at Intel's defaults until their meaning is confirmed.
    """

    HPL_PARAMETER_DEFAULTS = {
        **HPLApplication.HPL_PARAMETER_DEFAULTS,
        "NB": ("int", (256, 2048), 1536),
    }

    LAUNCH_PARAMETERS: dict[str, tuple[list[Any], Any]] = {
        "GRID": (["1x2", "2x1", "1x1"], "1x2"),
        "HUGEPAGES": (["off", "thp"], "off"),
        "MEM_POLICY": (["default", "interleave"], "default"),
    }

    def resolve_configuration(self, configuration: dict[str, Any]) -> dict[str, Any]:
        # Skip LocalHPLApplication's RANKS-based grid; the grid comes from GRID.
        resolved = HPLApplication.resolve_configuration(self, configuration)
        p, q = (int(v) for v in str(resolved["GRID"]).split("x"))
        resolved.update({"P": p, "Q": q, "RANKS": p * q})
        return resolved

    def launch_lines(self, configuration: dict[str, Any]) -> list[str]:
        c = {name: configuration.get(name, default) for name, (_, default) in self.LAUNCH_PARAMETERS.items()}
        p, q = (int(v) for v in str(c["GRID"]).split("x"))
        ranks = p * q
        n_numa = len(_cpu_topology())  # one NUMA node per socket here (SNC off)
        numa_per_rank = n_numa // ranks
        lines = [
            "for v in $(compgen -e | grep '^I_MPI_PIN'); do unset \"$v\"; done",
            "export I_MPI_DEBUG=4",
            # Each rank's MPI pin domain is the NUMA node(s) it owns; Intel's
            # HPL then threads across that domain's cores.
            "export I_MPI_PIN=1",
            f"export I_MPI_PIN_DOMAIN={'numa' if numa_per_rank == 1 else 'node'}",
            f"export MPI_PER_NODE={ranks} NUMA_PER_MPI={numa_per_rank}",
            # The runner pins these to 1 for the MPI-only netlib build; Intel's
            # HPL must be free to thread across its NUMA node.
            "unset OMP_NUM_THREADS MKL_NUM_THREADS",
            # Intel's runme_intel64_prv: rank r owns NUMA nodes
            # [r*NUMA_PER_MPI, (r+1)*NUMA_PER_MPI - 1].
            "cat > \"$RUNDIR/intel_rank.sh\" <<'RANK'",
            "#!/bin/bash",
            "r=${MPI_LOCALRANKID:-${PMI_RANK:-0}}",
            "export HPL_HOST_NODE=$((r * NUMA_PER_MPI))-$(((r + 1) * NUMA_PER_MPI - 1))",
            'echo "intel_rank: rank $r HPL_HOST_NODE=$HPL_HOST_NODE"',
            'exec "$@"',
            "RANK",
            "chmod +x \"$RUNDIR/intel_rank.sh\"",
        ]
        if c["HUGEPAGES"] == "thp":
            lines.append("export GLIBC_TUNABLES=glibc.malloc.hugetlb=1")
        else:
            lines.append("unset GLIBC_TUNABLES")
        prefix = self._MEM_POLICY_PREFIX[c["MEM_POLICY"]]
        lines.append(f"{self.mpirun} -np {ranks} -perhost {ranks} \"$RUNDIR/intel_rank.sh\" {prefix}{self.executable}")
        return lines


class IntelHPLApplicationV2(IntelHPLApplication):
    """Intel HPL with Intel's documented launch variables (type ``hpl_intel_v2``).

    Adds to ``hpl_intel``:

    * ``GRID`` up to 8 ranks (2x2, 1x4, 4x1, 2x4, 4x2). Up to one rank per
      NUMA node uses ``HPL_HOST_NODE`` as before; more ranks split each node's
      cores into equal contiguous blocks given to each rank via
      ``HPL_HOST_CORE`` (verified: each rank threads on exactly its block).
    * ``HPL_LARGEPAGE`` 0/1: 1 maps memory with explicit 2 MB huge pages
      (hugetlbfs). It fails ("Memory Allocation Error") unless enough pages
      are reserved (vm.nr_hugepages), so the job checks first and fails in
      seconds. Note Intel's HPL already backs ~all its memory with
      transparent huge pages at HPL_LARGEPAGE=0.
    * ``HPL_LOG`` 0/1/2 (not tuned; recorded). 2 is the most informative: every
      process-column root prints progress, plus solve start/end times. Unset
      behaves like 1.
    """

    LAUNCH_PARAMETERS: dict[str, tuple[list[Any], Any]] = {
        "GRID": (["1x1", "1x2", "2x1", "2x2", "1x4", "4x1", "2x4", "4x2"], "1x1"),
        "MEM_POLICY": (["default", "interleave"], "default"),
        "HPL_LARGEPAGE": ([0, 1], 0),
        "HPL_LOG": ([0, 1, 2], 2),
        # Kept for record compatibility; Intel's HPL already uses THP itself.
        "HUGEPAGES": (["off", "thp"], "off"),
    }

    def launch_lines(self, configuration: dict[str, Any]) -> list[str]:
        c = {name: configuration.get(name, default) for name, (_, default) in self.LAUNCH_PARAMETERS.items()}
        p, q = (int(v) for v in str(c["GRID"]).split("x"))
        ranks = p * q
        numa = _numa_cores()                        # NUMA nodes -> physical cores (SNC-aware)
        n_numa = len(numa)

        def mask(cpus: list[int]) -> str:
            return hex(sum(1 << cpu for cpu in cpus))

        lines = [
            "for v in $(compgen -e | grep '^I_MPI_PIN'); do unset \"$v\"; done",
            "export I_MPI_DEBUG=4",
            "export I_MPI_PIN=1",
            # The runner pins these to 1 for the MPI-only netlib build; Intel's
            # HPL must be free to thread across its cores.
            "unset OMP_NUM_THREADS MKL_NUM_THREADS",
            f"export HPL_LOG={int(c['HPL_LOG'])} HPL_LARGEPAGE={int(c['HPL_LARGEPAGE'])}",
        ]
        if ranks <= n_numa:
            if n_numa % ranks:
                raise ValueError(f"GRID {c['GRID']}: {n_numa} NUMA nodes do not split evenly over {ranks} ranks")
            numa_per_rank = n_numa // ranks
            groups = [sum(numa[r * numa_per_rank:(r + 1) * numa_per_rank], []) for r in range(ranks)]
            lines += [
                # Each rank's pin domain = the cores of the NUMA node(s) it owns.
                "export I_MPI_PIN_DOMAIN=[" + ",".join(mask(g) for g in groups) + "]",
                f"export NUMA_PER_MPI={numa_per_rank}",
            ]
            pick = ('export HPL_HOST_NODE=$((r * NUMA_PER_MPI))-$(((r + 1) * NUMA_PER_MPI - 1))\n'
                    'echo "intel_rank: rank $r HPL_HOST_NODE=$HPL_HOST_NODE"')
        else:
            if ranks % n_numa:
                raise ValueError(f"GRID {c['GRID']}: {ranks} ranks do not split evenly over {n_numa} NUMA nodes")
            per_numa = ranks // n_numa
            blocks = []
            for cores in numa:
                size = len(cores) // per_numa
                blocks += [cores[i * size:(i + 1) * size] for i in range(per_numa)]
            lines.append("export I_MPI_PIN_DOMAIN=[" + ",".join(mask(b) for b in blocks) + "]")
            table = " ".join(f"{b[0]}-{b[-1]}" for b in blocks)
            pick = (f"cores=({table})\n"
                    'export HPL_HOST_CORE=${cores[$r]}\n'
                    'echo "intel_rank: rank $r HPL_HOST_CORE=$HPL_HOST_CORE"')
        lines += [
            "cat > \"$RUNDIR/intel_rank.sh\" <<'RANK'",
            "#!/bin/bash",
            "r=${MPI_LOCALRANKID:-${PMI_RANK:-0}}",
            *pick.split("\n"),
            'exec "$@"',
            "RANK",
            "chmod +x \"$RUNDIR/intel_rank.sh\"",
        ]
        if int(c["HPL_LARGEPAGE"]):
            need_mb = int(configuration["N"]) ** 2 * 8 // 2**20 * 105 // 100
            lines += [
                "free_mb=$(( $(awk '/HugePages_Free/{print $2}' /proc/meminfo) * "
                "$(awk '/Hugepagesize/{print $2}' /proc/meminfo) / 1024 ))",
                f'if [ "$free_mb" -lt {need_mb} ]; then echo "HPL_LARGEPAGE=1 needs ~{need_mb} MB of reserved '
                'huge pages, only ${free_mb} MB free (vm.nr_hugepages); skipping run"; exit 1; fi',
            ]
        if c["HUGEPAGES"] == "thp":
            lines.append("export GLIBC_TUNABLES=glibc.malloc.hugetlb=1")
        else:
            lines.append("unset GLIBC_TUNABLES")
        prefix = self._MEM_POLICY_PREFIX[c["MEM_POLICY"]]
        lines.append(f"{self.mpirun} -np {ranks} -perhost {ranks} \"$RUNDIR/intel_rank.sh\" {prefix}{self.executable}")
        return lines


def _build_local_hpl(config: ExperimentConfig) -> LocalHPLApplication:
    return LocalHPLApplication.for_benchmark(
        executable=os.path.expandvars(config.executable) if config.executable else None,
        node_memory_bytes=config.node_memory_bytes,
        memory_factor=config.memory_factor,
        memory_fraction_bounds=config.memory_fraction_bounds,
        ntasks=config.slurm.ntasks,
        tunable=config.tunable,
        fixed=config.fixed,
    )


common.register_application("hpl_local", _build_local_hpl)


def _build_intel_hpl(config: ExperimentConfig) -> IntelHPLApplication:
    return IntelHPLApplication.for_benchmark(
        executable=os.path.expandvars(config.executable) if config.executable else None,
        node_memory_bytes=config.node_memory_bytes,
        memory_factor=config.memory_factor,
        memory_fraction_bounds=config.memory_fraction_bounds,
        ntasks=config.slurm.ntasks,
        tunable=config.tunable,
        fixed=config.fixed,
    )


common.register_application("hpl_intel", _build_intel_hpl)


def _build_intel_hpl_v2(config: ExperimentConfig) -> IntelHPLApplicationV2:
    return IntelHPLApplicationV2.for_benchmark(
        executable=os.path.expandvars(config.executable) if config.executable else None,
        node_memory_bytes=config.node_memory_bytes,
        memory_factor=config.memory_factor,
        memory_fraction_bounds=config.memory_fraction_bounds,
        ntasks=config.slurm.ntasks,
        tunable=config.tunable,
        fixed=config.fixed,
    )


common.register_application("hpl_intel_v2", _build_intel_hpl_v2)


def _validate_grid(config: dict[str, Any], slurm: Any) -> None:
    """HPC-Tools checks P*Q against the fixed slurm.ntasks; with a tunable
    rank count the grid has to match the evaluation's RANKS instead."""
    ranks = int(config.get("RANKS", slurm.ntasks))
    if int(config["P"]) * int(config["Q"]) != ranks:
        raise ValueError(f"HPL grid {config['P']} x {config['Q']} does not match {ranks} ranks")


common._validate_grid = _validate_grid


if __name__ == "__main__":
    sys.exit(autotune_local.main(description="SMAC3 autotuning of HPL on Launchpad"))
