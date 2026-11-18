#!/usr/bin/env python3
"""One-off move of outputs/ from the flat layout (before 2026-10-31) to one
directory per benchmark (see outputs/README.md and paths.py).

    python3 scripts/Launchpad/migrate_outputs.py           # show the plan
    python3 scripts/Launchpad/migrate_outputs.py --apply   # do it (stop tuners and loops first)

Moves are renames on one filesystem. Every old -> new path is appended to
outputs/archive/MOVED.tsv. Finished bulk data nothing reads is packed into
.tar.zst files and the originals removed only after the archive lists every
file and directory. Superseded and invalid run groups go to outputs/archive/.
Job scripts and their stdout/stderr (outputs/slurm/job_<id>.*) go to the
benchmark of the run group whose record names that job id.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from paths import OUTPUTS, bench_of

MOVES: list[tuple[Path, Path]] = []
PACKS: list[tuple[Path, list[str], Path]] = []    # (cwd, members relative to cwd, archive)


def move(old: str, new: str) -> None:
    src = OUTPUTS / old
    if src.exists() or src.is_symlink():
        MOVES.append((src, OUTPUTS / new))


def plan() -> None:
    # first: outputs/stream (the old sweep) becomes the STREAM benchmark directory
    move("stream", "stream_sweep.tmp")
    at = OUTPUTS / "autotuning"
    # run-group records, application logs and tmux/console logs
    for entry in sorted(at.iterdir()) if at.is_dir() else []:
        name = entry.name
        if name in ("invalid_socket1_only", "superseded"):
            move(f"autotuning/{name}", f"archive/hpl/autotuning/{name}")
        elif name == ".node.lock":
            continue
        elif entry.is_dir():
            move(f"autotuning/{name}", f"{bench_of(name)}/autotuning/{name}")
        else:                                               # *_tmux.log, mfc_*.log
            stem = name.removesuffix(".log").removesuffix("_tmux")
            bench = "hpl" if stem.startswith("hpl_") else bench_of(stem)
            move(f"autotuning/{name}", f"{bench}/autotuning/{name}")
    for entry in sorted(OUTPUTS.iterdir()):
        name = entry.name
        if name in ("smac3_launchpad", "smac3_launchpad_bind"):    # logs of the invalid / superseded groups
            move(name, f"archive/hpl/{name}")
        elif name.startswith(("smac3_intel_hpl", "smac3_launchpad", "stream_smac3", "ngc_stream_smac3", "mfc_grid_",
                              "mfc_smac3", "mfc_igr")) and entry.is_dir():
            move(name, f"{bench_of(name)}/{name}")
    # fixed-layout directories
    for old, new in (("stream_sweep.tmp", "stream/sweep"), ("stream_cont", "stream/cont"),
                     ("stream_best", "stream/runbest/legacy"), ("hpl_bign", "hpl/bign"), ("system_ab", "hpl/system_ab"),
                     ("ngc_hpl", "ngc/hpl"), ("mfc_runs", "mfc/runs"), ("mfc_tuning_runs", "mfc/tuning_runs"),
                     ("descriptions.txt", "archive/descriptions.txt")):
        if old == "stream_sweep.tmp" or (OUTPUTS / old).exists():
            MOVES.append((OUTPUTS / old, OUTPUTS / new))
    nccl = OUTPUTS / "nccl_launchpad"
    for entry in sorted(nccl.iterdir()) if nccl.is_dir() else []:
        move(f"nccl_launchpad/{entry.name}", f"nccl/{entry.name}")
    # runbest runs (STREAM's ~42k loop directories are packed instead)
    rb = OUTPUTS / "runbest"
    stream_dirs = []
    for entry in sorted(rb.iterdir()) if rb.is_dir() else []:
        n = entry.name
        if n.startswith("stream_"):
            stream_dirs.append(n)
        else:
            bench = "hpl" if n.startswith(("hpl_intel", "hpl_netlib")) else "ngc" if n.startswith("ngc_") else n.split("_")[0]
            move(f"runbest/{n}", f"{bench}/runbest/{n}")
    if stream_dirs:
        PACKS.append((rb, stream_dirs, OUTPUTS / "stream/cont/runbest_stream_dirs_until_20261031.tar.zst"))
    # job scripts: by the run group that recorded the job id
    owner: dict[str, str] = {}
    for log in at.rglob("evaluations.jsonl") if at.is_dir() else []:
        group = log.parent.name
        archived = bool({"invalid_socket1_only", "superseded"} & set(log.relative_to(at).parts))
        for line in log.read_text().splitlines():
            if line.strip():
                job = json.loads(line).get("job_id")
                if job:
                    owner[str(job)] = "archive/hpl/slurm" if archived else f"{bench_of(group)}/slurm"
    slurm = OUTPUTS / "slurm"
    attempts = []
    for entry in sorted(slurm.iterdir()) if slurm.is_dir() else []:
        n = entry.name
        if n.startswith("job_attempt_"):
            attempts.append(n)
            continue
        job = n.removeprefix("job_").split(".")[0]
        move(f"slurm/{n}", f"{owner.get(job, 'archive/slurm_unmatched')}/{n}")
    if attempts:
        PACKS.append((slurm, attempts, OUTPUTS / "archive/slurm_job_attempt_templates.tar.zst"))
    # leftover per-run work directories of killed tuner runs
    for old, new in (("hpl_runs", "archive/hpl_runs_leftovers.tar.zst"), ("stream_runs", "archive/stream_runs_leftovers.tar.zst")):
        if (OUTPUTS / old).is_dir():
            PACKS.append((OUTPUTS, [old], OUTPUTS / new))


def count(cwd: Path, members: list[str]) -> int:
    n = 0
    for m in members:
        p = cwd / m
        n += 1
        if p.is_dir() and not p.is_symlink():
            for _root, dirs, files in os.walk(p):
                n += len(dirs) + len(files)
    return n


def pack(cwd: Path, members: list[str], archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    listfile = archive.with_suffix(".members")
    listfile.write_text("\n".join(members) + "\n")
    subprocess.run(["tar", "-C", str(cwd), "-I", "zstd -T0 -3", "-cf", str(archive), "-T", str(listfile)], check=True)
    listed = subprocess.run(["tar", "-I", "zstd", "-tf", str(archive)], check=True, capture_output=True, text=True).stdout
    have, want = len(listed.splitlines()), count(cwd, members)
    listfile.unlink()
    if have != want:
        sys.exit(f"{archive}: lists {have} entries, expected {want}; originals kept")
    for m in members:                                   # verified: remove the originals
        p = (cwd / m).resolve()
        assert p.is_relative_to(OUTPUTS) and p != OUTPUTS, p
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    print(f"packed {len(members)} entries ({want} files and dirs) -> {archive.relative_to(OUTPUTS)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true")
    apply = ap.parse_args().apply
    plan()
    for src, dst in MOVES:
        if dst.exists():
            sys.exit(f"target exists, nothing moved: {dst}")
    summary: dict[str, int] = {}
    for src, dst in MOVES:
        key = f"{src.relative_to(OUTPUTS).parts[0]} -> {'/'.join(dst.relative_to(OUTPUTS).parts[:2])}"
        summary[key] = summary.get(key, 0) + 1
    for key, n in sorted(summary.items()):
        print(f"  move {n:6d}  {key}")
    for cwd, members, archive in PACKS:
        print(f"  pack {len(members):6d}  {cwd.relative_to(OUTPUTS) if cwd != OUTPUTS else '.'}/ -> {archive.relative_to(OUTPUTS)}")
    if not apply:
        print("plan only; run with --apply to do it")
        return 0
    (OUTPUTS / "archive").mkdir(exist_ok=True)
    with open(OUTPUTS / "archive/MOVED.tsv", "a") as log:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        for src, dst in MOVES:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            if dst.name != "sweep" or src.name != "stream_sweep.tmp":
                log.write(f"{stamp}\toutputs/{src.relative_to(OUTPUTS)}\toutputs/{dst.relative_to(OUTPUTS)}\n")
        log.write(f"{stamp}\toutputs/stream (STREAM sweep)\toutputs/stream/sweep\n")
    print(f"moved {len(MOVES)} paths (list: outputs/archive/MOVED.tsv)")
    for cwd, members, archive in PACKS:
        pack(cwd, members, archive)
    for d in ("autotuning", "slurm", "runbest", "nccl_launchpad"):
        p = OUTPUTS / d
        if p.is_dir():
            left = [e.name for e in p.iterdir() if e.name != ".node.lock"]
            if left:
                print(f"left in outputs/{d}/: {left[:5]}{' ...' if len(left) > 5 else ''}")
            else:
                (p / ".node.lock").unlink(missing_ok=True)
                p.rmdir()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
