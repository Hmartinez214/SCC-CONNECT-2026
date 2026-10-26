"""Tests for the Launchpad tuning code (no benchmarks run).

    venv/bin/python -m pytest scripts/Launchpad/tests -q
"""

from __future__ import annotations

import json
import signal
import subprocess
import time
from pathlib import Path

import pytest

import autotune_local as al
import hpl_autotune as hpl
import stream_autotune as sa
from hpc_autotuner.experiments import common
from hpc_autotuner.experiments.config import ExperimentConfig
from hpc_autotuner.storage.filesystem import FilesystemStorage


def _sudo_ok() -> bool:
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


needs_sudo = pytest.mark.skipif(not _sudo_ok(), reason="needs passwordless sudo (job scripts use sudo -n tee)")


# ---------------------------------------------------------------------------
# per-job system settings are always restored
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_system(tmp_path: Path) -> dict[str, Path]:
    for pkg in ("package_00_die_00", "package_01_die_00"):
        (tmp_path / pkg).mkdir()
        (tmp_path / pkg / "max_freq_khz").write_text("2000000\n")
        (tmp_path / pkg / "min_freq_khz").write_text("800000\n")
    paths = {"thp": tmp_path / "thp", "nr": tmp_path / "nr_hugepages", "meminfo": tmp_path / "meminfo",
             "drop": tmp_path / "drop_caches", "compact": tmp_path / "compact_memory"}
    paths["thp"].write_text("always [madvise] never\n")
    paths["nr"].write_text("0\n")
    paths["drop"].write_text("")
    paths["compact"].write_text("")
    paths["meminfo"].write_text("HugePages_Total: 999999\nHugePages_Free: 999999\n")
    return {"root": tmp_path, **paths}


def _job(fake: dict[str, Path], body: str, pages: int = 100) -> Path:
    lines = [
        "set -euo pipefail",
        *al.cleanup_header(),
        *al.thp_lines("always", str(fake["thp"])),
        *al.hugepage_lines(pages, str(fake["nr"]), str(fake["meminfo"]), str(fake["drop"]), str(fake["compact"])),
        *al.uncore_lines(1200, str(fake["root"] / "package_*_die_*")),
        f'echo "during $(cat {fake["root"]}/package_00_die_00/max_freq_khz) $(cat {fake["thp"]}) $(cat {fake["nr"]})"',
        body,
    ]
    script = fake["root"] / "job.sh"
    script.write_text("\n".join(lines) + "\n")
    return script


def _state(fake: dict[str, Path]) -> tuple[str, str, str]:
    return ((fake["root"] / "package_00_die_00" / "max_freq_khz").read_text().strip(),
            fake["thp"].read_text().strip(), fake["nr"].read_text().strip())


@needs_sudo
@pytest.mark.parametrize("body,expected_rc", [("true", 0), ("false", 1), ("sleep 30", 143)])
def test_job_settings_restored(fake_system, body, expected_rc):
    proc = subprocess.Popen(["bash", str(_job(fake_system, body))], stdout=subprocess.PIPE, text=True)
    if expected_rc == 143:
        time.sleep(2)
        proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=60)
    assert proc.returncode == expected_rc
    assert "during 1200000 always 100" in out
    # Restored: THP back to the mode found (the real sysfs file then shows
    # "always [madvise] never"; the fake keeps the word written).
    assert _state(fake_system) == ("2000000", "madvise", "0")


@needs_sudo
def test_hugepage_shortfall_fails_fast_and_restores(fake_system):
    fake_system["meminfo"].write_text("HugePages_Total: 10\nHugePages_Free: 10\n")
    proc = subprocess.run(["bash", str(_job(fake_system, "true", pages=5000))], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 1
    assert "only 10 of 5000 huge pages" in proc.stdout
    assert _state(fake_system) == ("2000000", "madvise", "0")


@needs_sudo
def test_power_settings_restored(fake_system):
    root = fake_system["root"]
    for cpu, epb in ((0, "6"), (1, "7")):                     # mixed old values come back per file
        (root / f"cpu{cpu}" / "power").mkdir(parents=True)
        (root / f"cpu{cpu}" / "power" / "energy_perf_bias").write_text(epb + "\n")
        for i, name in enumerate(("POLL", "C1", "C1E", "C6")):
            (root / f"cpu{cpu}" / f"state{i}").mkdir()
            (root / f"cpu{cpu}" / f"state{i}" / "name").write_text(name + "\n")
            (root / f"cpu{cpu}" / f"state{i}" / "disable").write_text("0\n")
    unc = str(root / "package_*_die_*")
    script = root / "power.sh"
    script.write_text("\n".join([
        "set -euo pipefail", *al.cleanup_header(),
        *al.uncore_lines(1200, unc, pin=True),
        *al.epb_lines(0, str(root / "cpu*/power/energy_perf_bias")),
        *al.cstate_lines(["C1E", "C6"], str(root / "cpu*/state*")),
        f'echo "during $(cat {root}/package_00_die_00/min_freq_khz) $(cat {root}/cpu1/power/energy_perf_bias) '
        f'$(cat {root}/cpu1/state3/disable) $(cat {root}/cpu1/state1/disable)"',
    ]) + "\n")
    out = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "during 1200000 0 1 0" in out.stdout                # C1 itself stays enabled
    read = lambda rel: (root / rel).read_text().strip()        # noqa: E731
    assert [read("package_00_die_00/min_freq_khz"), read("package_00_die_00/max_freq_khz")] == ["800000", "2000000"]
    assert [read("cpu0/power/energy_perf_bias"), read("cpu1/power/energy_perf_bias")] == ["6", "7"]
    assert {read(f"cpu{c}/state{i}/disable") for c in (0, 1) for i in range(4)} == {"0"}


# ---------------------------------------------------------------------------
# HPL
# ---------------------------------------------------------------------------


@pytest.fixture
def intel_app(monkeypatch):
    # 2 sockets x 4 cores; core c's hyperthread sibling is c + 8.
    topo = [[[c, c + 8] for c in range(0, 4)], [[c, c + 8] for c in range(4, 8)]]
    monkeypatch.setattr(hpl, "_cpu_topology", lambda: topo)
    monkeypatch.setattr(hpl, "_numa_cores", lambda: [[0, 1, 2, 3], [4, 5, 6, 7]])
    return hpl.IntelHPLApplication.for_benchmark(
        executable="/x/xhpl", node_memory_bytes=2 * 1024**3, memory_fraction_bounds=(0.5, 0.6), ntasks=2,
        tunable=["N", "NB", *hpl.IntelHPLApplication.LAUNCH_PARAMETERS],
    )


def _launch(app, **over) -> list[str]:
    base = {p.name: (p.choices[0] if p.choices else int(p.bounds[0])) for p in app.tunable_parameters}
    return app.launch_lines(app.resolve_configuration({**base, **over}))


def _domains(lines: list[str]) -> list[int]:
    dom = next(l for l in lines if l.startswith("export I_MPI_PIN_DOMAIN=")).split("=", 1)[1]
    return [int(m, 16) for m in dom.strip("[]").split(",")]


def test_intel_grid_placement(intel_app):
    assert _domains(_launch(intel_app, GRID="1x1")) == [0xFF]
    assert _domains(_launch(intel_app, GRID="1x2")) == [0x0F, 0xF0]
    lines = _launch(intel_app, GRID="2x2", HPL_HT=1)
    assert "cores=(0-1,8-9 2-3,10-11 4-5,12-13 6-7,14-15)" in lines
    assert _domains(lines) == [0x303, 0xC0C, 0x3030, 0xC0C0]


@pytest.mark.parametrize("value,expected", [("all", "unset HPL_NUMTHREADS"), ("-1", "export HPL_NUMTHREADS=7"),
                                            ("1/2", "export HPL_NUMTHREADS=4")])
def test_intel_numthreads_relative_to_rank_cpus(intel_app, value, expected):
    assert expected in _launch(intel_app, GRID="1x1", HPL_NUMTHREADS=value)


def test_intel_uncore_switch_and_script_syntax(intel_app, tmp_path):
    cfg = intel_app.resolve_configuration({p.name: (p.choices[0] if p.choices else int(p.bounds[0]))
                                           for p in intel_app.tunable_parameters} | {"HPL_LARGEPAGE": 1})
    assert any("max_freq_khz" in l for l in intel_app.launch_lines(cfg))
    intel_app.manage_uncore = False
    assert not any("max_freq_khz" in l for l in intel_app.launch_lines(cfg))
    script = tmp_path / "cmd.sh"
    script.write_text(intel_app.command(cfg))
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0


def test_grid_must_match_ranks():
    slurm = type("S", (), {"ntasks": 64})
    hpl._validate_grid({"P": 2, "Q": 2, "RANKS": 4}, slurm)
    with pytest.raises(ValueError):
        hpl._validate_grid({"P": 2, "Q": 2, "RANKS": 8}, slurm)


@pytest.mark.parametrize("check,ok", [("PASSED", True), ("FAILED", False)])
def test_hpl_residual_must_pass(check, ok):
    out = ("WC00C2R2       93233   172    16     8             169.45             3.1885e+03\n"
           f"||Ax-b||_oo/(eps*(||A||_oo*||x||_oo+||b||_oo)*N)=   1.1e-03 ...... {check}\n")
    parsed = hpl.LocalHPLApplication(executable="/x").parse_result(out)
    assert parsed["success"] is ok
    assert (parsed["objective"] == pytest.approx(3188.5)) if ok else parsed["objective"] is None


# ---------------------------------------------------------------------------
# STREAM
# ---------------------------------------------------------------------------


@pytest.fixture
def stream_app(tmp_path):
    src = tmp_path / "stream.c"
    src.write_text("int main(){}\n")
    return sa.StreamApplication(source=src, mpi_source=tmp_path / "stream_mpi.c")


def test_stream_command_sets_and_restores(stream_app, tmp_path):
    base = {p.name: p.choices[0] for p in stream_app.tunable_parameters}
    cmd = stream_app.command(stream_app.resolve_configuration({**base, "THP": "always", "UNCORE_MAX_MHZ": 1200}))
    assert "_cleanup+=(_thp_restore)" in cmd and "_cleanup+=(_unc_restore)" in cmd
    assert "echo always | sudo -n tee" in cmd and "echo 1200000 | sudo -n tee" in cmd
    script = tmp_path / "cmd.sh"
    script.write_text(cmd)
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0


@pytest.mark.parametrize("value,line", [("default", "unset KMP_BLOCKTIME"), ("0", "export KMP_BLOCKTIME=0"),
                                        ("infinite", "export KMP_BLOCKTIME=infinite")])
def test_stream_blocktime(stream_app, value, line):
    base = {p.name: p.choices[0] for p in stream_app.tunable_parameters}
    assert line in stream_app.command(stream_app.resolve_configuration({**base, "KMP_BLOCKTIME": value})).split("\n")


def test_stream_mpi_launch(stream_app):
    base = {p.name: p.choices[0] for p in stream_app.tunable_parameters}
    one = stream_app.command(stream_app.resolve_configuration({**base, "OMP_THREADS": 64})).split("\n")
    assert "mpirun" not in "\n".join(one) and "export OMP_NUM_THREADS=64" in one
    assert any(l.startswith("icx ") and l.endswith('stream.c -o "$RUNDIR/stream.exe"') for l in one)
    cfg = stream_app.resolve_configuration({**base, "OMP_THREADS": 8, "MPI_RANKS": 16,
                                            "MPI_PIN_DOMAIN": "numa", "MPI_PIN_ORDER": "scatter",
                                            "NUMA": "interleave_all"})
    lines = stream_app.command(cfg).split("\n")
    assert any(l.startswith("mpiicx ") and l.endswith('stream_mpi.c -o "$RUNDIR/stream.exe"') for l in lines)
    assert "export OMP_NUM_THREADS=8" in lines and cfg["total_threads"] == 128
    assert "echo 'MPI: 16 ranks x 8 threads = 128 total'" in lines
    assert {"export I_MPI_PIN_DOMAIN=numa", "export I_MPI_PIN_ORDER=scatter", "export I_MPI_FABRICS=shm"} <= set(lines)
    assert 'mpirun -np 16 numactl --interleave=all "$RUNDIR/stream.exe"' in lines


def test_stream_smac_never_suggests_too_many_threads(stream_app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # SMAC writes smac3_output/ to the cwd
    opt = al.ResumableSMAC3Optimizer(stream_app.tunable_parameters, seed=1, n_trials=1000,
                                     forbidden=stream_app.forbidden_clauses)
    pairs = {(c["MPI_RANKS"], c["OMP_THREADS"]) for c in opt.cs.sample_configuration(2000)}
    assert max(r * t for r, t in pairs) <= 128
    assert (64, 2) in pairs and (1, 128) in pairs          # the limit itself is allowed
    base = {p.name: p.choices[0] for p in stream_app.tunable_parameters}
    with pytest.raises(AssertionError):
        stream_app.resolve_configuration({**base, "MPI_RANKS": 64, "OMP_THREADS": 4})


def test_stream_parse():
    rows = "".join(f"{k}:  {v}  0.1  0.1  0.2\n" for k, v in
                   (("Copy", 400000.0), ("Scale", 390000.0), ("Add", 450000.0), ("Triad", 460000.5)))
    assert sa.StreamApplication.parse_result(None, rows + "Solution Validates\n")["objective"] == 460000.5
    assert sa.StreamApplication.parse_result(None, rows)["success"] is False


# ---------------------------------------------------------------------------
# priors and replay
# ---------------------------------------------------------------------------


def _stream_config(app, **over) -> dict:
    return app.resolve_configuration({**{p.name: p.choices[0] for p in app.tunable_parameters}, **over})


def test_import_evaluations_fills_and_skips(stream_app, tmp_path):
    new = ("UNCORE_MAX_MHZ", "THP", "KMP_BLOCKTIME", "MPI_RANKS", "MPI_PIN_DOMAIN", "MPI_PIN_ORDER",
           "UNCORE_PIN", "EPB", "CSTATES")
    old = {k: v for k, v in _stream_config(stream_app).items() if k not in new}
    log = tmp_path / "evaluations.jsonl"
    rows = [
        {"attempt": 1, "status": "COMPLETED", "success": True, "objective": 4e5, "configuration": old,
         "submitted_at": "2026-10-15T10:00:00"},
        {"attempt": 2, "status": "COMPLETED", "success": True, "objective": 3e5, "configuration": old,
         "submitted_at": "2026-10-19T04:00:00"},
        {"attempt": 3, "status": "COMPLETED", "success": True, "objective": 1e5,
         "configuration": {**old, "ARRAY_SIZE": 1000}},          # outside the space
        {"attempt": 4, "status": "SUBMITTED", "configuration": old, "job_id": "x"},   # interrupted
        {"attempt": 5, "status": "COMPLETED", "success": True, "objective": 3e5,       # v3: total threads
         "configuration": {**old, "MPI_RANKS": 32, "MPI_PIN_DOMAIN": "numa", "MPI_PIN_ORDER": "scatter",
                           "OMP_THREADS": 64, "threads_per_rank": 2}},
    ]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    imported = stream_app.prior_records(log)
    assert [r["attempt"] for r in imported] == [1, 2, 5]
    assert imported[2]["configuration"]["OMP_THREADS"] == 2 and "threads_per_rank" not in imported[2]["configuration"]
    imported = imported[:2]
    assert [r["configuration"]["UNCORE_MAX_MHZ"] for r in imported] == [2500, 2000]
    assert all(r["configuration"]["THP"] == "madvise" and r["configuration"]["KMP_BLOCKTIME"] == "default"
               and r["configuration"]["MPI_RANKS"] == 1 and r["configuration"]["EPB"] == 6
               and r["configuration"]["CSTATES"] == "all" and r["job_id"] is None for r in imported)


def test_replay_round_trip(stream_app, tmp_path):
    common.register_application("stream_test", lambda _cfg: stream_app)
    storage = FilesystemStorage(root=tmp_path / "rg", run_group="rg")  # creates the directory
    sizes = stream_app.PARAMETERS["ARRAY_SIZE"][0][:5]
    for attempt, size in enumerate(sizes, 1):
        storage.append_evaluation({
            "attempt": attempt, "status": "COMPLETED", "success": True, "objective": 1e5 * attempt,
            "configuration": _stream_config(stream_app, ARRAY_SIZE=size), "job_id": str(attempt),
        })
    config = ExperimentConfig(optimizer="smac3", run_group="rg", objective_metric="triad_mb_s",
                              application_type="stream_test")
    assert al.check_replay(config, storage, stream_app) == 0


# -- NGC STREAM ---------------------------------------------------------------

import ngc_stream_autotune as ngc  # noqa: E402


def test_ngc_stream_command_and_parse(tmp_path):
    app = ngc.NGCStreamApplication("img:tag")
    script = app.command(app.resolve_configuration(
        {"ARRAY_SIZE": 0, "DTYPE": "fp32", "DEVICE": 1, "SM_CLOCK_MHZ": 0, "POWER_LIMIT_W": 310}))
    assert "--d 1 --dt fp32" in script and "--n " not in script and "-rgc" in script
    (tmp_path / "job.sh").write_text(script)
    subprocess.run(["bash", "-n", str(tmp_path / "job.sh")], check=True)
    with pytest.raises(AssertionError):
        app.resolve_configuration({"ARRAY_SIZE": 2_147_483_648, "DTYPE": "fp64", "DEVICE": 0,
                                   "SM_CLOCK_MHZ": 0, "POWER_LIMIT_W": 400})
    rows = "".join(f"{k}:  3500000.5  0.001  0.001  0.001\n" for k in ngc.KERNELS)
    assert app.parse_result(rows)["objective"] == 3500000.5
    assert app.parse_result(rows.replace("Triad", "Tri"))["success"] is False
