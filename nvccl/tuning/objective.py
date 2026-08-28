"""Black-box objective for tuning NCCL env vars: given a config dict, run one
nccl-tests collective over a size range and return peak in-place busbw (GB/s).

Shared by tune_smac.py; also runnable directly:
    python tuning/objective.py all_reduce
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from parse_nccl import parse  # noqa: E402


def run(cfg, collective="all_reduce", ngpus=2, beg="256M", end="8G",
        iters=20, warmup=10, timeout=900):
    """cfg: dict of NCCL_* -> value (None/'' entries skipped). Returns float GB/s.
    Raises RuntimeError if the run produced no parseable rows or failed validation."""
    env = os.environ.copy()
    for k, v in cfg.items():
        if v is not None and str(v) != "":
            env[str(k)] = str(v)
    binp = ROOT / "nccl-tests" / "build" / f"{collective}_perf"
    if not binp.exists():
        raise RuntimeError(f"{binp} missing - run scripts/build.sh")
    cmd = [str(binp), "-b", str(beg), "-e", str(end), "-f", "2",
           "-g", str(ngpus), "-n", str(iters), "-w", str(warmup), "-c", "1"]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    res = parse(p.stdout + "\n" + p.stderr)
    if not res["rows"]:
        raise RuntimeError((p.stdout + p.stderr)[-1200:])
    for r in res["rows"]:
        for w in (r["oop_wrong"], r["ip_wrong"]):
            if w not in ("0", "N/A", None):
                raise RuntimeError(f"validation failed at size {r['size']}: wrong={w}")
    return float(res["peak_busbw"] or 0.0)


if __name__ == "__main__":
    coll = sys.argv[1] if len(sys.argv) > 1 else "all_reduce"
    print(f"{coll}: baseline peak busbw = {run({}, collective=coll):.1f} GB/s")
