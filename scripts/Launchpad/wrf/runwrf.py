#!/usr/bin/env python3
"""Run one timed WRF case on the Launchpad node and report its speed.

    python scripts/Launchpad/wrf/runwrf.py --ranks 64
    python scripts/Launchpad/wrf/runwrf.py --ranks 32 --threads 2 --nproc-x 4 --tiles 2 --bind core

The case (default outputs/wrf/case/santacruz: GFS-driven, 9 km d01 + 3 km
d02 nest, 100x100x45 each) is linked into a fresh run directory, its
namelist.input is rewritten for the run length, decomposition and tiling,
and wrf.exe runs under mpirun. History and restart output are switched off
(a single wrfout at t=0 is still written during init), so the number is
the model's integration speed.

Speed is measured from rsl.error.0000's "Timing for main" lines: every
d01 step after the first SKIP of them (warm-up). Each d01 step's line covers
that step including its nested d02 steps, so the objective is

    speed = simulated seconds / wall seconds   (higher is better)

over the counted d01 steps. Also reported: total wall time of wrf.exe.

Prints "WRF_RESULT {json}" as the last line and writes result.json into the
run directory. --keep keeps rsl.* only (wrfout is always deleted).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CASE = REPO / "outputs/wrf/case/santacruz"
TABLES = REPO / "libs/opt/WRF/run"
#: Builds: gnu = the team's first build (gfortran 13, dmpar, -O2);
#: gnu_dmsm = dm+sm (MPI + OpenMP), -O3 -march=sapphirerapids (see README.md).
EXES = {"gnu": REPO / "outputs/wrf/bin/wrf_gnu.exe",
        "gnu_dmsm": REPO / "outputs/wrf/bin/wrf_gnu_dmsm.exe"}
MPIRUN = "/usr/bin/mpirun"  # system Open MPI 4.1.6: what wrf_gnu.exe links against
LOGICAL_CPUS = 128
CORES = 64


def set_namelist(text: str, group: str, key: str, value: str) -> str:
    """Set key in &group (replacing it if present), keeping everything else."""
    m = re.search(rf"(?ims)^\s*&{group}\b(.*?)^\s*/", text)
    if not m:
        raise ValueError(f"no &{group} in namelist")
    body = m.group(1)
    line = f" {key:<24} = {value},\n"
    pat = re.compile(rf"(?im)^\s*{key}\s*=.*\n")
    body = pat.sub(line, body, count=1) if pat.search(body) else body + line
    return text[:m.start(1)] + body + text[m.end(1):]


def make_namelist(src: str, a: argparse.Namespace) -> str:
    nl = src
    tc = [("run_days", "0"), ("run_hours", str(a.hours)), ("run_minutes", "0"),
          ("end_year", "2026, 2026"), ("end_month", "09, 09"), ("end_day", "27, 27"),
          ("end_hour", f"{a.hours:02d}, {a.hours:02d}"),
          ("history_interval", "100000, 100000"), ("restart_interval", "100000"),
          ("restart", ".false.")]
    for k, v in tc:
        nl = set_namelist(nl, "time_control", k, v)
    nl = set_namelist(nl, "domains", "max_dom", str(a.max_dom))
    if a.nproc_x > 0:
        nl = set_namelist(nl, "domains", "nproc_x", str(a.nproc_x))
        nl = set_namelist(nl, "domains", "nproc_y", str(a.ranks // a.nproc_x))
    nl = set_namelist(nl, "domains", "numtiles", str(a.tiles))
    if a.tile_strategy:
        nl = set_namelist(nl, "domains", "tile_sz_x", "0")
        nl = set_namelist(nl, "domains", "tile_sz_y", "0")
    for kv in a.param:
        group, rest = kv.split(":", 1)
        k, v = rest.split("=", 1)
        nl = set_namelist(nl, group, k, v)
    return nl


def binding(a: argparse.Namespace) -> list[str]:
    """mpirun mapping/binding options for Open MPI 4.1. With threads > 1
    each rank gets PE=threads consecutive slots (cores, or hyperthreads when
    a.ht) and its OpenMP threads stay inside them."""
    if a.bind == "none":
        return ["--bind-to", "none"]
    hw = ["--use-hwthread-cpus"] if a.ht else []
    unit = "hwthread" if a.ht else "core"
    pe = f":PE={a.threads}" if a.threads > 1 else ""
    if a.bind == "core":        # consecutive ranks on consecutive cores
        return hw + ["--map-by", f"{'slot' if pe else unit}{pe}", "--bind-to", unit]
    if a.bind == "socket":      # ranks alternate between sockets
        return hw + ["--map-by", f"socket{pe}", "--bind-to", unit, "--rank-by", "core" if not pe else "slot"]
    if a.bind == "numa":        # each rank may float over its whole NUMA node
        return hw + ["--map-by", f"numa{pe}", "--bind-to", "numa"]
    raise ValueError(a.bind)


def parse_rsl(text: str, skip: int, dt: float) -> dict:
    d1 = [float(x) for x in re.findall(r"Timing for main: time \S+ on domain\s+1:\s+([0-9.]+) elapsed", text)]
    d2 = [float(x) for x in re.findall(r"Timing for main: time \S+ on domain\s+2:\s+([0-9.]+) elapsed", text)]
    ok = "SUCCESS COMPLETE WRF" in text
    counted = d1[skip:]
    out = {"success": ok and len(counted) > 0, "d01_steps": len(d1), "d02_steps": len(d2)}
    if counted:
        wall = sum(counted)
        out.update({"counted_steps": len(counted), "counted_wall_s": wall,
                    "mean_d01_step_s": wall / len(counted),
                    "speed": len(counted) * dt / wall,
                    "d02_sum_s": sum(d2)})
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--exe", default="gnu", help="build: " + ", ".join(EXES) + " (or a path)")
    p.add_argument("--case", type=Path, default=CASE)
    p.add_argument("--ranks", type=int, default=64)
    p.add_argument("--threads", type=int, default=1, help="OpenMP threads per rank (OpenMP builds only)")
    p.add_argument("--nproc-x", type=int, default=0, help="ranks in x (0 = WRF picks)")
    p.add_argument("--decomp", choices=["auto", "wide", "tall"], default="auto",
                   help="nearest-to-square factorisation with nproc_x >= nproc_y (wide) or <= (tall)")
    p.add_argument("--tiles", type=int, default=1, help="numtiles per patch")
    p.add_argument("--tile-strategy", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--bind", choices=["core", "socket", "numa", "none"], default="core")
    p.add_argument("--ht", action="store_true",
                   help="hyperthreads as slots (automatic when ranks x threads > 64)")
    p.add_argument("--hours", type=int, default=3, help="forecast length (hours, < 24)")
    p.add_argument("--max-dom", type=int, default=2)
    p.add_argument("--skip", type=int, default=2, help="d01 steps not counted (warm-up)")
    p.add_argument("--param", action="append", default=[], help="GROUP:KEY=VALUE namelist override")
    p.add_argument("--env", action="append", default=[], help="KEY=VALUE for the run")
    p.add_argument("--prefix", default="", help="command prefix for wrf.exe (e.g. 'numactl --interleave=all')")
    p.add_argument("--run-dir", type=Path)
    p.add_argument("--keep", action="store_true")
    p.add_argument("--timeout", type=float, default=1800)
    a = p.parse_args()

    if a.ranks * a.threads > CORES:
        a.ht = True
    if a.decomp != "auto" and not a.nproc_x:
        lo = max(d for d in range(1, int(a.ranks ** 0.5) + 1) if a.ranks % d == 0)
        a.nproc_x = a.ranks // lo if a.decomp == "wide" else lo
    slots = LOGICAL_CPUS if a.ht else CORES
    if a.ranks * a.threads > slots:
        p.error(f"{a.ranks} ranks x {a.threads} threads > {slots} slots (use --ht for hyperthreads)")
    if a.nproc_x and a.ranks % a.nproc_x:
        p.error("--nproc-x must divide --ranks")
    exe = Path(EXES.get(a.exe, a.exe)).resolve()

    label = f"r{a.ranks}t{a.threads}x{a.nproc_x}n{a.tiles}{a.bind}{'ht' if a.ht else ''}_{time.strftime('%Y%m%d-%H%M%S')}"
    run = (a.run_dir or REPO / "outputs/wrf/runs" / label).resolve()
    run.mkdir(parents=True, exist_ok=True)
    for f in TABLES.iterdir():
        if f.is_file() and not f.name.startswith(("rsl.", "namelist")) and not os.access(f, os.X_OK):
            (run / f.name).symlink_to(f)
    for f in a.case.iterdir():
        if f.name != "namelist.input":
            dst = run / f.name
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            dst.symlink_to(f.resolve())
    nl = make_namelist((a.case / "namelist.input").read_text(), a)
    (run / "namelist.input").write_text(nl)
    dt = float(re.search(r"(?im)^\s*time_step\s*=\s*([0-9.]+)", nl).group(1))

    env = dict(os.environ)
    env.update({"OMP_NUM_THREADS": str(a.threads), "OMP_STACKSIZE": "64M",
                "OMP_PROC_BIND": "close", "OMP_PLACES": "cores" if not a.ht else "threads"})
    for kv in a.env:
        k, v = kv.split("=", 1)
        env[k] = v
    cmd = [MPIRUN, "-np", str(a.ranks), *binding(a), "--report-bindings",
           "-x", "OMP_NUM_THREADS", "-x", "OMP_STACKSIZE", "-x", "OMP_PROC_BIND", "-x", "OMP_PLACES",
           *[x for kv in a.env for x in ("-x", kv.split("=", 1)[0])],
           *shlex.split(a.prefix), str(exe)]
    (run / "command.txt").write_text(shlex.join(cmd) + "\n")
    print("run dir:", run, flush=True)
    print("command:", shlex.join(cmd), flush=True)

    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=run, env=env, stdout=open(run / "mpirun.log", "w"),
                              stderr=subprocess.STDOUT, timeout=a.timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
    wall = time.time() - t0

    rsl = (run / "rsl.error.0000").read_text(errors="replace") if (run / "rsl.error.0000").exists() else ""
    res = parse_rsl(rsl, a.skip, dt)
    res.update({"returncode": rc, "total_wall_s": wall, "exe": str(exe), "ranks": a.ranks,
                "threads": a.threads, "nproc_x": a.nproc_x, "decomp_opt": a.decomp, "tiles": a.tiles, "bind": a.bind,
                "ht": a.ht, "hours": a.hours, "max_dom": a.max_dom, "params": a.param,
                "env": a.env, "prefix": a.prefix, "run_dir": str(run)})
    if rc != 0:
        res["success"] = False
    m = re.search(r"Ntasks in X\s+(\d+)\s*,\s*ntasks in Y\s+(\d+)", rsl)
    if m:
        res["decomp"] = f"{m.group(1)}x{m.group(2)}"
    (run / "result.json").write_text(json.dumps(res, indent=1) + "\n")

    for f in run.iterdir():
        if f.name.startswith("wrfout") or f.name.startswith("wrfrst") or (
                f.is_symlink() and not a.keep):
            f.unlink()
    if not a.keep:
        for f in run.glob("rsl.*"):
            if f.name != "rsl.error.0000":
                f.unlink()
    print("WRF_RESULT " + json.dumps(res), flush=True)
    return 0 if res["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
