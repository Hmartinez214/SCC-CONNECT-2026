"""Shared pieces for running the HPC-Tools autotuner (SMAC3) on Launchpad.

HPC-Tools' controller is Slurm-backed; Launchpad has no Slurm. The drivers in
hpl/hpl_autotune.py and stream/stream_autotune.py reuse the framework's
experiment loop (``run_experiment``) and SMAC3 adapter unchanged and plug in
only what this node needs:

* ``LocalScheduler`` -- runs each rendered child job script with bash on this
  node instead of ``sbatch`` (same SLURM_* variables the template expects),
  one job at a time across *all* tuners via a node-wide lock, so two tuners
  can run side by side without benchmarking on top of each other.
* ``ResumableSMAC3Optimizer`` -- warm-starts SMAC from ``evaluations.jsonl``
  so a cancelled/crashed run picks up where it left off with its full
  history (and any imported prior results).
* ``main()`` -- the controller entry point, including ``--check-replay``,
  which audits what SMAC actually received from the log without running jobs.
* Job-script helpers for machine-wide settings a run may change (uncore
  frequency cap, THP mode, reserved huge pages), each restored on exit.
* ``final_records`` / ``import_evaluations`` -- reading a run group's
  evaluations.jsonl and seeding a new run group from it.

A driver registers its application with HPC-Tools' ``register_application``
and calls ``main()``. If the application has a ``prior_records(path)``
method and the YAML sets ``experiment.priors``, those records seed an empty
run group (see ``_seed_priors``).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from hpc_autotuner.experiments import common
from hpc_autotuner.experiments.config import ExperimentConfig
from hpc_autotuner.optimizers.smac3 import SMAC3Optimizer
from hpc_autotuner.schedulers.base import Scheduler
from hpc_autotuner.core.space import ParameterSpace
from hpc_autotuner.storage.filesystem import FilesystemStorage

# ---------------------------------------------------------------------------
# scheduler: run child job scripts locally
# ---------------------------------------------------------------------------


def _parse_walltime(value: str | None) -> float | None:
    if not value:
        return None
    parts = [int(p) for p in str(value).split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3:]
    return float(h * 3600 + m * 60 + s)


def _session_members(sid: int) -> list[int]:
    """PIDs of every live process in session ``sid`` (from /proc)."""
    pids = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        # Fields after the ")" closing comm: state ppid pgrp session ...
        fields = stat.rsplit(")", 1)[1].split()
        if fields[0] != "Z" and int(fields[3]) == sid:
            pids.append(int(entry.name))
    return pids


def _kill_session(sid: int, grace: float = 30.0) -> bool:
    """SIGTERM every process in session ``sid``, SIGKILL after ``grace``
    seconds, and return once none are left. True if anything was running."""
    found = False
    deadline = time.monotonic() + grace
    while members := _session_members(sid):
        found = True
        sig = signal.SIGTERM if time.monotonic() < deadline else signal.SIGKILL
        for pid in members:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        time.sleep(1)
    return found


class LocalScheduler(Scheduler):
    """Run job scripts with bash on this node, one at a time node-wide.

    Each child job runs in its own session (Intel MPI gives every rank its own
    process group, but they stay in that session), so the session is what gets
    cleaned up. Its id is written to ``pidfile`` so a controller that was
    SIGKILLed leaves nothing behind once the next controller starts.

    ``node_lock`` is held from submit until the job is gone; every tuner on
    the node uses the same file, so their benchmark runs never overlap.
    """

    def __init__(self, project_root: Path, slurm: Any, pidfile: Path, node_lock: Path) -> None:
        self.project_root = project_root
        self.ntasks = int(slurm.ntasks)
        self.nodes = int(slurm.nodes)
        self.timeout = _parse_walltime(slurm.time)
        self.pidfile = pidfile
        self.node_lock = node_lock
        self.out_dir = project_root / "outputs" / "slurm"
        self._lock_fh: Any = None
        self._procs: dict[str, subprocess.Popen] = {}
        self._states: dict[str, str] = {}

    def kill_stale(self) -> None:
        try:
            sid = int(self.pidfile.read_text().strip())
        except (FileNotFoundError, ValueError):
            return
        # Only touch the session if it still runs one of our job scripts (the
        # id may have been reused since).
        marker = str(self.out_dir / "job_").encode()
        ours = False
        for pid in _session_members(sid):
            try:
                if marker in Path(f"/proc/{pid}/cmdline").read_bytes():
                    ours = True
                    break
            except OSError:
                continue
        if ours and _kill_session(sid, grace=5):
            print(f"[local] killed leftover job (session {sid})", flush=True)
        self.pidfile.unlink(missing_ok=True)

    def _acquire_node(self) -> None:
        self.node_lock.parent.mkdir(parents=True, exist_ok=True)
        self._lock_fh = open(self.node_lock, "w")
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[local] node busy with another tuner's job; waiting for it", flush=True)
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX)

    def _release_node(self) -> None:
        if self._lock_fh is not None:
            self._lock_fh.close()
            self._lock_fh = None

    def submit(self, script: str | Path, environment: dict[str, str] | None = None) -> str:
        self._acquire_node()
        stamp = time.strftime("%Y%m%d%H%M%S")
        job_id, n = stamp, 0
        while (self.out_dir / f"job_{job_id}.out").exists():
            n += 1
            job_id = f"{stamp}{n:02d}"
        env = dict(os.environ)
        env.update(environment or {})
        env.update({
            "SLURM_SUBMIT_DIR": str(self.project_root),
            "SLURM_JOB_ID": job_id,
            "SLURM_NTASKS": str(self.ntasks),
            "SLURM_JOB_NUM_NODES": str(self.nodes),
        })
        self.out_dir.mkdir(parents=True, exist_ok=True)
        # The controller names scripts job_attempt_<N>.sh in a directory shared
        # by every tuner; run a private copy so another tuner's attempt N can
        # never rewrite a script bash is still reading.
        private = self.out_dir / f"job_{job_id}.sh"
        shutil.copyfile(script, private)
        stdout = open(self.out_dir / f"job_{job_id}.out", "w")
        stderr = open(self.out_dir / f"job_{job_id}.err", "w")
        proc = subprocess.Popen(
            ["bash", str(private)],
            cwd=self.project_root,
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        stdout.close()
        stderr.close()
        self.pidfile.write_text(str(proc.pid))
        self._procs[job_id] = proc
        return job_id

    def wait(self, job_id: str) -> None:
        proc = self._procs[job_id]
        try:
            rc = proc.wait(timeout=self.timeout)
            self._states[job_id] = "COMPLETED" if rc == 0 else "FAILED"
        except subprocess.TimeoutExpired:
            self._states[job_id] = "TIMEOUT"
        # Also reaps stragglers (mpirun/ranks) after a normal exit.
        self.kill(job_id)
        self.pidfile.unlink(missing_ok=True)
        self._release_node()

    def status(self, job_id: str) -> str:
        if job_id in self._states:
            return self._states[job_id]
        return "RUNNING" if self._procs[job_id].poll() is None else "COMPLETED"

    def kill(self, job_id: str | None = None) -> None:
        for jid in [job_id] if job_id else list(self._procs):
            proc = self._procs.get(jid)
            if proc is not None:
                _kill_session(proc.pid)
                proc.poll()


# ---------------------------------------------------------------------------
# per-job system settings (shell lines for job scripts)
# ---------------------------------------------------------------------------
#
# Machine-wide settings a job may change for its own run. Every change
# registers a restore function in _cleanup, which an EXIT trap runs (normal
# exit, error, or the scheduler's SIGTERM), so a job never leaves the node
# changed. Paths are overridable for tests.

UNCORE_SYSFS = "/sys/devices/system/cpu/intel_uncore_frequency/package_*_die_*"
THP_ENABLED = "/sys/kernel/mm/transparent_hugepage/enabled"
NR_HUGEPAGES = "/proc/sys/vm/nr_hugepages"
EPB_SYSFS = "/sys/devices/system/cpu/cpu*/power/energy_perf_bias"
CPUIDLE_SYSFS = "/sys/devices/system/cpu/cpu*/cpuidle/state*"
MEMINFO = "/proc/meminfo"
DROP_CACHES = "/proc/sys/vm/drop_caches"
COMPACT_MEMORY = "/proc/sys/vm/compact_memory"


def cleanup_header() -> list[str]:
    return [
        "_cleanup=()",
        "trap 'for _f in \"${_cleanup[@]}\"; do \"$_f\"; done' EXIT",
        "trap 'exit 143' TERM INT HUP",
    ]


def uncore_lines(mhz: int, sysfs: str = UNCORE_SYSFS, pin: bool = False) -> list[str]:
    """Cap the uncore (mesh/LLC/memory controller) max frequency; with
    ``pin``, also raise its min to the cap so it never clocks down. Restore
    after (min first, so min <= max holds at every step)."""
    lines = [
        f"_unc=({sysfs})",
        '_unc_found=(); _unc_min=(); for _d in "${_unc[@]}"; do _unc_found+=("$(cat "$_d/max_freq_khz")"); '
        '_unc_min+=("$(cat "$_d/min_freq_khz")"); done',
        "_unc_restore() { local i=0; for _d in \"${_unc[@]}\"; do "
        "echo \"${_unc_min[$i]}\" | sudo -n tee \"$_d/min_freq_khz\" >/dev/null; "
        "echo \"${_unc_found[$i]}\" | sudo -n tee \"$_d/max_freq_khz\" >/dev/null; i=$((i + 1)); done; }",
        "_cleanup+=(_unc_restore)",
        f'for _d in "${{_unc[@]}}"; do echo {mhz * 1000} | sudo -n tee "$_d/max_freq_khz" >/dev/null '
        f'|| {{ echo "cannot set uncore max to {mhz} MHz (needs passwordless sudo)"; exit 1; }}; done',
        f'echo "uncore max: {mhz} MHz (was ${{_unc_found[*]}} kHz)"',
    ]
    if pin:
        lines += [
            f'for _d in "${{_unc[@]}}"; do echo {mhz * 1000} | sudo -n tee "$_d/min_freq_khz" >/dev/null '
            f'|| {{ echo "cannot pin uncore min to {mhz} MHz"; exit 1; }}; done',
            f'echo "uncore min: pinned to {mhz} MHz (was ${{_unc_min[*]}} kHz)"',
        ]
    return lines


def sysfs_lines(tag: str, value: str, what: str, files: str = "", select: list[str] | None = None) -> list[str]:
    """Write ``value`` to many sysfs files (a glob, or the bash array _<tag>
    filled by ``select``) with one sudo call; restore every file's old value
    after (one call per distinct old value)."""
    arr = f"_{tag}"
    return [
        *(select or [f"{arr}=({files})"]),
        f'{arr}_found=(); for _p in "${{{arr}[@]}}"; do {arr}_found+=("$(cat "$_p")"); done',
        f"{arr}_restore() {{ local v i _p; for v in $(printf '%s\\n' \"${{{arr}_found[@]}}\" | sort -u); do "
        f'local sel=(); i=0; for _p in "${{{arr}[@]}}"; do [[ "${{{arr}_found[$i]}}" == "$v" ]] && sel+=("$_p"); '
        f'i=$((i + 1)); done; echo "$v" | sudo -n tee "${{sel[@]}}" >/dev/null; done; }}',
        f"_cleanup+=({arr}_restore)",
        f'echo {value} | sudo -n tee "${{{arr}[@]}}" >/dev/null || {{ echo "cannot set {what} to {value}"; exit 1; }}',
        f'echo "{what}: {value} on ${{#{arr}[@]}} files (was $(printf \'%s\\n\' "${{{arr}_found[@]}}" | sort | uniq -c | xargs))"',
    ]


def epb_lines(value: int, sysfs: str = EPB_SYSFS) -> list[str]:
    """Energy-performance bias on every CPU (0 performance .. 15 powersave;
    the firmware also uses it for uncore and memory power management)."""
    return sysfs_lines("epb", str(value), "EPB", files=sysfs)


def cstate_lines(disable: list[str], sysfs: str = CPUIDLE_SYSFS) -> list[str]:
    """Disable the named idle states (e.g. C1E, C6) on every CPU; restore after."""
    if not disable:
        return []
    pattern = "|".join(disable)
    select = ["_cst=()", f'for _s in {sysfs}; do case "$(cat "$_s/name")" in {pattern}) _cst+=("$_s/disable");; esac; done']
    return sysfs_lines("cst", "1", f"{'/'.join(disable)} disable", select=select)


def thp_lines(mode: str, path: str = THP_ENABLED) -> list[str]:
    """Set the system transparent-huge-page mode (always | madvise | never);
    restore after. Unlike madvise, `always` also covers static (.bss) arrays."""
    return [
        f"_thp_found=$(sed -E 's/.*\\[([a-z]+)\\].*/\\1/' {path})",
        f'_thp_restore() {{ echo "$_thp_found" | sudo -n tee {path} >/dev/null; }}',
        "_cleanup+=(_thp_restore)",
        f'echo {mode} | sudo -n tee {path} >/dev/null || {{ echo "cannot set THP to {mode}"; exit 1; }}',
        f'echo "THP: {mode} (was $_thp_found)"',
    ]


def gpu_lines(device: int, sm_mhz: int, power_w: int) -> list[str]:
    """Lock one GPU's SM clock (0 = leave it unlocked) and set its power
    limit, with persistence mode on so both survive until the benchmark's
    CUDA context opens; restore all three after (clocks back to unlocked)."""
    q = f"nvidia-smi -i {device} --format=csv,noheader,nounits --query-gpu"
    lines = [
        f"_gpu_pm=$({q}=persistence_mode)",
        f"_gpu_pl=$({q}=power.limit)",
        f"_gpu_restore() {{ sudo -n nvidia-smi -i {device} -rgc >/dev/null; "
        f'sudo -n nvidia-smi -i {device} -pl "$_gpu_pl" >/dev/null; '
        f'[[ "$_gpu_pm" == Enabled ]] || sudo -n nvidia-smi -i {device} -pm 0 >/dev/null; }}',
        "_cleanup+=(_gpu_restore)",
        f'sudo -n nvidia-smi -i {device} -pm 1 >/dev/null || {{ echo "cannot enable persistence mode on GPU {device}"; exit 1; }}',
        f'sudo -n nvidia-smi -i {device} -pl {power_w} >/dev/null || {{ echo "cannot set GPU {device} power limit to {power_w} W"; exit 1; }}',
    ]
    if sm_mhz:
        lines.append(f'sudo -n nvidia-smi -i {device} -lgc {sm_mhz},{sm_mhz} >/dev/null '
                     f'|| {{ echo "cannot lock GPU {device} SM clock to {sm_mhz} MHz"; exit 1; }}')
    else:
        lines.append(f"sudo -n nvidia-smi -i {device} -rgc >/dev/null")
    lines.append(f'echo "GPU {device}: SM clock {sm_mhz or "unlocked"}, power limit {power_w} W '
                 f'(was $_gpu_pl W, persistence $_gpu_pm)"')
    return lines


def hugepage_lines(pages: int, nr_hugepages: str = NR_HUGEPAGES, meminfo: str = MEMINFO,
                   drop_caches: str = DROP_CACHES, compact_memory: str = COMPACT_MEMORY) -> list[str]:
    """Reserve ``pages`` 2 MB hugetlbfs pages for this job (dropping caches and
    compacting memory first so they can be found), fail in seconds if the
    kernel cannot provide them, and put the old reservation back after."""
    return [
        f"_hp_found=$(cat {nr_hugepages})",
        f'_hp_restore() {{ echo "$_hp_found" | sudo -n tee {nr_hugepages} >/dev/null; }}',
        "_cleanup+=(_hp_restore)",
        f"sync; echo 3 | sudo -n tee {drop_caches} >/dev/null; echo 1 | sudo -n tee {compact_memory} >/dev/null",
        f"echo {pages} | sudo -n tee {nr_hugepages} >/dev/null",
        f"_hp_free=$(awk '/HugePages_Free/{{print $2}}' {meminfo})",
        f'echo "huge pages: asked {pages}, free $_hp_free"',
        f'if [ "$_hp_free" -lt {pages} ]; then echo "only $_hp_free of {pages} huge pages '
        'could be reserved; skipping run"; exit 1; fi',
    ]


# ---------------------------------------------------------------------------
# evaluation logs: reading them and seeding a run group from another one
# ---------------------------------------------------------------------------


def final_records(path: Path) -> dict[int, dict[str, Any]]:
    """attempt -> last record for that attempt (a log holds a SUBMITTED record
    and then the final one; only the last describes the attempt)."""
    final: dict[int, dict[str, Any]] = {}
    for line in Path(path).read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            final[int(rec.get("attempt", 0))] = rec
    return final


def import_evaluations(
    application: Any, path: Path,
    fill: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Finished runs of another run group, as prior records for this one.

    ``fill(record, configuration)`` adds values for knobs the old runs
    predate -- what they actually ran with. Fixed values fill the rest; runs
    outside this application's search space are skipped.
    """
    space = ParameterSpace(application.tunable_parameters)
    records, skipped = [], 0
    for attempt, rec in sorted(final_records(path).items()):
        if rec.get("status") not in common.TERMINAL_STATES or not attempt:
            continue
        rec = dict(rec)
        config = dict(rec["configuration"])
        if fill is not None:
            fill(rec, config)
        for name, value in application.fixed_values.items():
            config.setdefault(name, value)
        config = application.resolve_configuration(config)
        try:
            space.validate(config)
        except ValueError:
            skipped += 1
            continue
        rec["configuration"] = config
        rec["job_id"] = None
        rec.setdefault("metadata", {})["imported_from"] = f"{path}#attempt={attempt}"
        records.append(rec)
    if skipped:
        print(f"[priors] skipped {skipped} runs outside the current search space", flush=True)
    return records


# ---------------------------------------------------------------------------
# optimizer: SMAC3 that can be warm-started from the evaluation log
# ---------------------------------------------------------------------------


class ResumableSMAC3Optimizer(SMAC3Optimizer):
    """SMAC3 adapter with a smaller initial design and log-driven resume.

    ``set_replay()`` takes the logged tunable configurations, in the order the
    controller will replay them. While any are left, ``suggest()`` hands back
    the next *logged* configuration without calling SMAC's ask(), and
    ``observe()`` then tells SMAC its real result -- SMAC's documented
    warm-start path. (Asking and then telling a different configuration would
    leave the asked one in SMAC's runhistory as a phantom RUNNING trial.)

    The scenario is non-deterministic: benchmark timings are noisy, so SMAC's
    intensifier re-runs promising configurations on up to 3 "seeds" (the
    benchmark ignores the seed; it is just a repeat) and compares mean costs.
    The log does not record SMAC's seed, so a configuration's k-th logged run
    is told on the intensifier's k-th seed -- the order it schedules them in.
    """

    #: Configurations in the Sobol initial design before the random-forest
    #: model takes over (SMAC's default would be 10 per parameter). Replayed
    #: results count towards it.
    initial_design_size = 24

    def __init__(self, *args: Any, forbidden: Callable[[Any], list[Any]] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # forbidden(cs) -> ConfigSpace forbidden clauses: combinations SMAC
        # must never suggest (e.g. more threads than CPUs).
        if forbidden is not None:
            self.cs.add(forbidden(self.cs))
        self.replay: list[dict[str, Any]] = []
        self._replay_seen: dict[tuple, int] = {}
        self._build_facade(self.initial_design_size)

    def _build_facade(self, n_initial: int) -> None:
        from smac import HyperparameterOptimizationFacade, Scenario

        self.scenario = Scenario(
            self.cs, n_trials=self.scenario.n_trials, seed=self.seed or 0, deterministic=False
        )
        initial = HyperparameterOptimizationFacade.get_initial_design(
            self.scenario, n_configs=max(n_initial, 1)
        )
        self.smac = HyperparameterOptimizationFacade(
            self.scenario, lambda config, seed=0: 0.0, initial_design=initial, overwrite=True
        )
        # The intensifier initializes lazily on the first ask(); replay needs
        # its seed keys before that. (Its own later call is harmless: it only
        # re-reads the runhistory, and the seed keys are cached.)
        self.smac.intensifier.__post_init__()

    def set_replay(self, configurations: list[dict[str, Any]]) -> None:
        self.replay = list(configurations)
        # History already explores the space; only top up the initial design.
        self._build_facade(self.initial_design_size - len(self.replay))

    def suggest(self) -> dict[str, Any]:
        if not self.replay:
            return super().suggest()
        from ConfigSpace import Configuration
        from smac.runhistory import TrialInfo

        if self._pending is not None:
            raise RuntimeError("SMAC3 cannot suggest before the previous trial is observed.")
        logged = self.replay.pop(0)
        values = {name: logged.get(name) for name in self.cs.keys()}
        try:
            config = Configuration(self.cs, values=values)
        except Exception as exc:  # noqa: BLE001 - logged config no longer fits the space
            # The search space changed since this was logged (e.g. the YAML
            # was edited). Returning SMAC's own suggestion makes the
            # controller stop replaying; use a new run_group instead.
            print(f"[resume] logged configuration is outside the current search space ({exc}); "
                  "history will not be fully replayed.", flush=True)
            self.replay.clear()
            return super().suggest()
        keys = self.smac.intensifier.get_instance_seed_keys_of_interest()
        ident = tuple(values[name] for name in sorted(values))
        nth = self._replay_seen.get(ident, 0)
        self._replay_seen[ident] = nth + 1
        # Repeats beyond the intensifier's seeds are dropped by SMAC's
        # runhistory (one result per config/seed); pairing stays intact.
        key = keys[min(nth, len(keys) - 1)]
        self._pending = TrialInfo(config=config, instance=key.instance, seed=key.seed)
        return values

    def _to_loss(self, result: dict[str, Any]) -> float:
        # Failed/invalid runs score as an objective of 0 (e.g. 0 GFLOPs)
        # rather than the framework's 1e9 penalty, which would swamp the
        # random-forest surrogate.
        objective = result.get("objective")
        if objective is None or not result.get("success", False):
            return 0.0
        return super()._to_loss(result)


def _replay_records(storage: FilesystemStorage) -> list[dict[str, Any]]:
    """Records run_experiment() will replay, in replay order (the interrupted
    attempt, if any, last)."""
    by_attempt = final_records(storage.evaluations_file) if storage.evaluations_file.exists() else {}
    terminal = [
        by_attempt[a] for a in sorted(by_attempt)
        if by_attempt[a].get("status") in common.TERMINAL_STATES
    ]
    incomplete = next(
        (
            by_attempt[a] for a in sorted(by_attempt)
            if a > 0 and by_attempt[a].get("job_id")
            and by_attempt[a].get("status") not in common.TERMINAL_STATES
        ),
        None,
    )
    records = [r for r in terminal
               if "configuration" in r and (incomplete is None or r["attempt"] != incomplete["attempt"])]
    if incomplete is not None:
        records.append(incomplete)
    return records


def _seed_priors(storage: FilesystemStorage, application: Any, path: Path) -> int:
    """Write the application's prior records into an empty run group, as
    attempts 1..K, so the normal resume path replays them into SMAC."""
    if storage.read_evaluations() or not hasattr(application, "prior_records"):
        return 0
    records = application.prior_records(path)
    for attempt, rec in enumerate(records, 1):
        rec.update({"attempt": attempt, "evaluation_id": attempt, "run_group": storage.root.name})
        storage.append_evaluation(rec)
    return len(records)


# ---------------------------------------------------------------------------
# replay audit
# ---------------------------------------------------------------------------


def check_replay(config: ExperimentConfig, storage: FilesystemStorage, application: Any) -> int:
    """Replay a run group's log into a fresh optimizer exactly as a restart
    would (on a scratch copy, no jobs run) and compare what SMAC holds with
    what the log says. Returns 0 when everything matches."""
    records = _replay_records(storage)
    finished = [r for r in records if r.get("status") in common.TERMINAL_STATES]
    if not finished:
        print(f"run group:           {storage.root}\nRESULT:              OK (no finished runs to replay yet)")
        return 0
    started = time.monotonic()
    optimizer, warnings = _replay_into_fresh_optimizer(config, storage, application, finished)
    elapsed = time.monotonic() - started

    names = list(optimizer.cs.keys())

    def ident(cfg: dict[str, Any]) -> tuple:
        return tuple(_native(cfg.get(n)) for n in names)

    rh = optimizer.smac.runhistory
    n_seeds = len(optimizer.smac.intensifier.get_instance_seed_keys_of_interest())
    held: dict[tuple, list[float]] = {}
    running = 0
    for key, value in rh.items():
        if value.status.name == "RUNNING":
            running += 1
        else:
            held.setdefault(ident(dict(rh.get_config(key.config_id))), []).append(value.cost)
    logged: dict[tuple, list[float]] = {}
    for rec in finished:
        logged.setdefault(ident(rec["configuration"]), []).append(optimizer._to_loss(rec))
    expected = sum(min(len(c), n_seeds) for c in logged.values())
    dropped = sum(max(len(c) - n_seeds, 0) for c in logged.values())
    mismatches = _count_cost_mismatches(logged, held, n_seeds, names)

    metric = config.objective_metric
    best = min(finished, key=optimizer._to_loss) if finished else None
    incumbents = optimizer.smac.intensifier.get_incumbents()
    inc = incumbents[0] if incumbents else None
    inc_runs = logged.get(ident(dict(inc)), []) if inc is not None else []

    print(f"run group:           {storage.root}")
    print(f"log records:         {len(finished)} finished, {len(logged)} distinct configurations"
          + (", plus 1 interrupted (re-run on restart)" if len(records) > len(finished) else ""))
    print(f"SMAC runhistory:     {len(rh) - running} finished trials (expected {expected}"
          + (f"; {dropped} repeats beyond {n_seeds} seeds/config not held" if dropped else "")
          + f"), {running} running")
    print(f"cost mismatches:     {mismatches}")
    print(f"replay warnings:     {len(warnings)}" + (f" ({warnings[0]})" if warnings else ""))
    if best is not None:
        print(f"best single run:     attempt {best['attempt']}, {metric}={best.get('objective')}")
    if inc is not None:
        mean = -sum(inc_runs) / len(inc_runs) if inc_runs else float("nan")
        same = best is not None and ident(dict(inc)) == ident(best["configuration"])
        print(f"SMAC incumbent:      mean {metric}={mean:.6g} over {len(inc_runs)} run(s)"
              + (" (same configuration as best single run)" if same else ""))
    print(f"replay time:         {elapsed:.1f} s")
    ok = (mismatches == 0 and not warnings and running == 0
          and len(rh) == expected and (inc is None or ident(dict(inc)) in logged))
    print("RESULT:              " + ("OK" if ok else "PROBLEMS FOUND"))
    return 0 if ok else 1


def _replay_into_fresh_optimizer(config: ExperimentConfig, storage: FilesystemStorage, application: Any,
                                 finished: list[dict[str, Any]]) -> tuple["ResumableSMAC3Optimizer", list[str]]:
    """Run the controller's resume path on a scratch copy of the log with a
    budget that schedules no new jobs; return the primed optimizer and any
    replay warnings it logged."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_storage = FilesystemStorage(root=Path(tmp) / storage.root.name, run_group=storage.root.name)
        tmp_storage.root.mkdir(parents=True, exist_ok=True)
        for rec in finished:
            tmp_storage.append_evaluation(rec)
        cwd = os.getcwd()
        os.chdir(tmp)  # SMAC writes smac3_output/ relative to the cwd
        try:
            optimizer = ResumableSMAC3Optimizer(
                application.tunable_parameters, seed=config.seed,
                direction=config.objective_direction, n_trials=config.budget,
                forbidden=getattr(application, "forbidden_clauses", None),
            )
            optimizer.set_replay([r["configuration"] for r in finished])
            messages: list[str] = []
            common.run_experiment(
                optimizer=optimizer, application=application,
                scheduler=None,  # type: ignore[arg-type]  # budget == history: nothing is submitted
                storage=tmp_storage, budget=len(finished), seed=config.seed,
                run_group=storage.root.name, optimizer_name="smac3", slurm=config.slurm,
                output_root=Path(tmp), project_root=Path(tmp), log=messages.append,
            )
        finally:
            os.chdir(cwd)
    return optimizer, [m for m in messages if "warning" in m]


def _count_cost_mismatches(logged: dict[tuple, list[float]], held: dict[tuple, list[float]],
                           n_seeds: int, names: list[str]) -> int:
    mismatches = 0
    for cfg, costs in logged.items():
        got = sorted(held.get(cfg, []))
        want = sorted(costs[:n_seeds])
        if len(got) != len(want) or any(not math.isclose(g, w, rel_tol=1e-9, abs_tol=1e-9)
                                         for g, w in zip(got, want)):
            mismatches += 1
            if mismatches <= 5:
                print(f"  MISMATCH {dict(zip(names, cfg))}: log costs {want} vs SMAC {got}")
    return mismatches


def _native(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None, description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", required=True)
    parser.add_argument("--budget", type=int, default=None)
    parser.add_argument("--run-group", default=None)
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--check-replay", action="store_true",
                        help="Audit how the run group's log replays into SMAC, then exit (runs no jobs).")
    return parser.parse_args(argv)


def _load_config(args: argparse.Namespace) -> tuple[ExperimentConfig, str | None]:
    config = ExperimentConfig.from_yaml(args.config)
    if args.budget is not None:
        config.budget = args.budget
    if args.run_group:
        config.run_group = args.run_group
    if args.project_root:
        config.project_root = args.project_root
    # Not part of HPC-Tools' schema: prior results to seed a new run group.
    priors = (yaml.safe_load(Path(args.config).read_text()).get("experiment") or {}).get("priors")
    return config, priors


def _claim_run_group(storage: FilesystemStorage) -> Any:
    """One controller per run group at a time; returns the held lock (keep it
    referenced), or None if another controller has it."""
    lock = open(storage.root / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return None
    return lock


def _reset_affinity() -> None:
    """A restricted CPU mask is inherited by the benchmark and silently squeezes
    it onto those CPUs (the default tmux server here was started with socket 1
    only). Binding is the optimizer's job, so start from all CPUs."""
    all_cpus = set(range(os.cpu_count() or 1))
    inherited = os.sched_getaffinity(0)
    if inherited != all_cpus:
        print(f"[local] resetting inherited CPU affinity ({len(inherited)} of {len(all_cpus)} CPUs) "
              "to all CPUs", flush=True)
        os.sched_setaffinity(0, all_cpus)


def _stop_on_signals(scheduler: LocalScheduler) -> None:
    def on_signal(signum: int, _frame: Any) -> None:
        # Kill the job before printing: when the tmux session goes away the
        # tee reading our stdout dies too, and the print would raise.
        scheduler.kill()
        try:
            print(f"\n[local] caught signal {signum}; stopped the current job. Re-run to resume.", flush=True)
        except OSError:
            pass
        os._exit(128 + signum)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_signal)


def main(argv: list[str] | None = None, *, description: str = "") -> int:
    args = _parse_args(argv, description)
    config, priors = _load_config(args)
    root = config.resolved_project_root()
    output = config.resolved_output_root()
    run_group = config.run_group
    storage = FilesystemStorage(root=output / run_group, run_group=run_group)
    application = common.build_application(config)

    if args.check_replay:
        return check_replay(config, storage, application)

    storage.root.mkdir(parents=True, exist_ok=True)
    lock = _claim_run_group(storage)  # noqa: F841 - held for the controller's lifetime
    if lock is None:
        print(f"ERROR: another controller is already running run group '{run_group}'", file=sys.stderr)
        return 3

    if priors:
        seeded = _seed_priors(storage, application, (root / os.path.expandvars(priors)).resolve())
        if seeded:
            print(f"[priors] seeded '{run_group}' with {seeded} prior results from {priors}", flush=True)

    _reset_affinity()
    # SMAC writes its scratch output (smac3_output/) relative to the cwd; keep
    # it with the run group. Every other path used below is absolute.
    os.chdir(storage.root)

    scheduler = LocalScheduler(root, config.slurm, pidfile=storage.root / ".child_pgid",
                               node_lock=output / ".node.lock")
    scheduler.kill_stale()
    _stop_on_signals(scheduler)

    optimizer = ResumableSMAC3Optimizer(
        application.tunable_parameters, seed=config.seed,
        direction=config.objective_direction, n_trials=config.budget,
        forbidden=getattr(application, "forbidden_clauses", None),
    )
    optimizer.set_replay([r["configuration"] for r in _replay_records(storage)])
    print(f"project_root={root}\noutput={storage.root}\n"
          f"optimizer=smac3 budget={config.budget} seed={config.seed} "
          f"replaying={len(optimizer.replay)}", flush=True)

    common.run_experiment(
        optimizer=optimizer, application=application, scheduler=scheduler, storage=storage,
        budget=config.budget, seed=config.seed, run_group=run_group, optimizer_name="smac3",
        slurm=config.slurm, output_root=output, project_root=root,
    )
    return 0
