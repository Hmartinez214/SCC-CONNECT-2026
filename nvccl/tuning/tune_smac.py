#!/usr/bin/env python3
"""Autotune NCCL env vars with SMAC3 (Bayesian optimization).

Each trial sets a combination of NCCL_* vars, runs one nccl-tests collective
over a size range, and scores it by peak in-place busbw (maximized).

    pip install "smac>=2.1"
    python tuning/tune_smac.py --collective all_reduce --trials 80 \
                               --beg 256M --end 8G

Output -> tuning/smac_out/<name>/  (open later in DeepCAVE).
Prints the winning config as `export NCCL_...` lines at the end.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from objective import run  # noqa: E402

from ConfigSpace import Categorical, ConfigurationSpace, Integer  # noqa: E402
from smac import HyperparameterOptimizationFacade, Scenario  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ARGS = None


def config_space():
    cs = ConfigurationSpace(seed=0)
    cs.add([
        # Ring vs Tree: near-identical for 2 GPUs, matters at higher counts.
        Categorical("NCCL_ALGO", ["Ring", "Tree"], default="Ring"),
        # protocol: LL (low-latency, ~half BW), LL128 (NVLink mid), Simple (full BW)
        Categorical("NCCL_PROTO", ["Simple", "LL128", "LL"], default="Simple"),
        # parallel channels (SMs/link slices in flight)
        Integer("nchannels", (2, 32), default=8),
        # per-channel staging buffer
        Categorical("NCCL_BUFFSIZE",
                    ["1048576", "2097152", "4194304", "8388608", "16777216"],
                    default="4194304"),
        # CUDA threads per channel block
        Categorical("NCCL_NTHREADS", ["128", "256", "512", "640"], default="512"),
    ])
    return cs


def target(config, seed=0):
    d = dict(config)
    n = d.pop("nchannels")
    d["NCCL_MIN_NCHANNELS"] = n
    d["NCCL_MAX_NCHANNELS"] = n
    try:
        bw = run(d, collective=ARGS.collective, ngpus=ARGS.ngpus,
                 beg=ARGS.beg, end=ARGS.end)
    except Exception as e:                       # infeasible combo -> worst score
        print(f"  [infeasible] {d}: {str(e)[:200]}")
        return 1e3
    print(f"  busbw={bw:6.1f} GB/s   {d}")
    return -bw


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--collective", default="all_reduce")
    ap.add_argument("--ngpus", type=int, default=2)
    ap.add_argument("--trials", type=int, default=80)
    ap.add_argument("--beg", default="256M")
    ap.add_argument("--end", default="8G")
    ap.add_argument("--name", default=None)
    ARGS = ap.parse_args()
    name = ARGS.name or f"{ARGS.collective}_g{ARGS.ngpus}"

    scenario = Scenario(
        config_space(),
        name=name,
        output_directory=HERE / "smac_out",
        n_trials=ARGS.trials,
        deterministic=True,       # busbw ~ stable; set False to let SMAC re-eval
        n_workers=1,              # ONE benchmark at a time - they share the GPUs
    )
    smac = HyperparameterOptimizationFacade(scenario, target, overwrite=True)
    inc = smac.optimize()

    d = dict(inc)
    n = d.pop("nchannels")
    d["NCCL_MIN_NCHANNELS"] = d["NCCL_MAX_NCHANNELS"] = n
    print("\n=== best config ===")
    for k, v in d.items():
        print(f"export {k}={v}")
    print(f"# peak busbw = {-smac.runhistory.get_cost(inc):.1f} GB/s")
    print(f"# SMAC output: {scenario.output_directory}  (open in DeepCAVE)")


if __name__ == "__main__":
    main()
