#!/usr/bin/env python3
"""Grid-search NCCL settings for large-message busbw on the LaunchPad NVLink
pair, per collective, then write the winners for scripts/launchpad_sweep.sh.

    python scripts/launchpad_tune.py                 # full grid, 4 collectives
    python scripts/launchpad_tune.py --colls all_reduce --sizes 1G 8G

Grid (empty = NCCL's default):
    NCCL_MIN/MAX_NCHANNELS  16 24 32       (default picked 16 on this pair)
    NCCL_PROTO              "" Simple LL128
    NCCL_ALGO               "" Ring Tree   (all_reduce only; the others are Ring)
    NCCL_BUFFSIZE           "" (4 MiB) 16777216
    NCCL_NTHREADS           "" 256 512
    NCCL_P2P_READ_ENABLE    "" 1           (default writes; 1 = receiver reads)

Each point runs <coll>_perf -b <lo> -e <hi> -f 2 -g 2 -n 20 -w 5 -c 0 and is
scored by its best out-of-place busbw. Logs, results.csv and best.env land in
outputs/nccl_launchpad/tune_<stamp>/. Holds the node lock shared with the
autotuners. Resumes: points already in results.csv of --out are skipped.
"""
import argparse
import csv
import fcntl
import itertools
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NVCCL = os.path.dirname(HERE)
REPO = os.path.dirname(NVCCL)
sys.path.insert(0, HERE)
from parse_nccl import parse  # noqa: E402

COLLS = ["all_reduce", "all_gather", "reduce_scatter", "broadcast"]
GRID = {
    "NCHANNELS": ["16", "24", "32"],
    "NCCL_PROTO": ["", "Simple", "LL128"],
    "NCCL_ALGO": ["", "Ring", "Tree"],
    "NCCL_BUFFSIZE": ["", "16777216"],
    "NCCL_NTHREADS": ["", "256", "512"],
    "NCCL_P2P_READ_ENABLE": ["", "1"],
}
KEYS = list(GRID)


def points(coll):
    grid = dict(GRID)
    if coll != "all_reduce":
        grid["NCCL_ALGO"] = [""]
    for values in itertools.product(*(grid[k] for k in KEYS)):
        yield dict(zip(KEYS, values))


def env_for(p):
    env = {}
    for k, v in p.items():
        if not v:
            continue
        if k == "NCHANNELS":
            env["NCCL_MIN_NCHANNELS"] = env["NCCL_MAX_NCHANNELS"] = v
        else:
            env[k] = v
    return env


def tag(coll, p):
    return coll + "".join(f"_{k.replace('NCCL_', '').lower()}-{v}" for k, v in p.items() if v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--colls", nargs="+", default=COLLS)
    ap.add_argument("--sizes", nargs=2, default=["1G", "8G"])
    ap.add_argument("--pair", default="0,1")
    ap.add_argument("--out", default=os.path.join(REPO, "outputs", "nccl_launchpad",
                                                  time.strftime("tune_%Y%m%d_%H%M%S")))
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, "logs"), exist_ok=True)
    results = os.path.join(a.out, "results.csv")
    done = set()
    if os.path.exists(results):
        done = {r["tag"] for r in csv.DictReader(open(results))}
    new = not os.path.exists(results)
    fh = open(results, "a", newline="")
    w = csv.writer(fh)
    if new:
        w.writerow(["tag", "coll", *KEYS, "peak_busbw", "peak_size", "busbw_at_max", "wrong", "rc"])

    lock = open(os.path.join(REPO, "outputs", "autotuning", ".node.lock"), "w")
    fcntl.flock(lock, fcntl.LOCK_EX)

    base = {k: v for k, v in os.environ.items() if not k.startswith("NCCL_") or k == "NCCL_HOME"}
    todo = [(c, p) for c in a.colls for p in points(c) if tag(c, p) not in done]
    print(f"{len(todo)} points to run -> {a.out}", flush=True)
    for i, (coll, p) in enumerate(todo, 1):
        t = tag(coll, p)
        env = dict(base, CUDA_VISIBLE_DEVICES=a.pair, NCCL_DEBUG="WARN", **env_for(p))
        cmd = [os.path.join(NVCCL, "nccl-tests", "build", f"{coll}_perf"), "-b", a.sizes[0], "-e", a.sizes[1],
               "-f", "2", "-g", "2", "-n", "20", "-w", "5", "-c", "0"]
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
        log = os.path.join(a.out, "logs", t + ".log")
        with open(log, "w") as f:
            f.write("# " + " ".join(f"{k}={v}" for k, v in env_for(p).items()) + " " + " ".join(cmd) + "\n")
            f.write(r.stdout + r.stderr)
        rows = [x for x in parse(r.stdout)["rows"] if x["oop_busbw"] is not None]
        best = max(rows, key=lambda x: x["oop_busbw"]) if rows else None
        wrong = sum(int(x["oop_wrong"]) for x in rows if x["oop_wrong"].isdigit())
        w.writerow([t, coll, *(p[k] for k in KEYS), best and best["oop_busbw"], best and best["size"],
                    rows[-1]["oop_busbw"] if rows else None, wrong, r.returncode])
        fh.flush()
        print(f"[{i}/{len(todo)}] {t}: {best and best['oop_busbw']} GB/s rc={r.returncode}", flush=True)

    # winners per collective
    rows = [r for r in csv.DictReader(open(results)) if r["rc"] == "0" and r["peak_busbw"]]
    with open(os.path.join(a.out, "best.env"), "w") as f:
        f.write("# <collective> <NCCL_* settings>; best peak out-of-place busbw from results.csv\n")
        for coll in COLLS:
            mine = [r for r in rows if r["coll"] == coll]
            if not mine:
                continue
            b = max(mine, key=lambda r: float(r["peak_busbw"]))
            dflt = [r for r in mine if not any(r[k] for k in KEYS if k != "NCHANNELS") and r["NCHANNELS"] == "16"]
            settings = " ".join(f"{k}={v}" for k, v in env_for({k: b[k] for k in KEYS}).items())
            f.write(f"{coll} {settings}\n")
            print(f"best {coll}: {b['peak_busbw']} GB/s ({settings})"
                  + (f"  vs 16ch default {dflt[0]['peak_busbw']}" if dflt else ""))


if __name__ == "__main__":
    main()
