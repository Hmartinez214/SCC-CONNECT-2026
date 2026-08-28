#!/usr/bin/env python3
"""Plot nccl-tests results from scripts/bench.sh and scripts/sweep.sh.

    module load python          # Perlmutter: provides matplotlib
    python scripts/plot.py                      # busbw + latency curves (newest run per collective)
    python scripts/plot.py results/20260828-1200 [more dirs...]
    python scripts/plot.py results/sweep-20260828-1300     # sweep bar chart

PNGs are written into results/ (or the given dir). Falls back to an ASCII
table if matplotlib is missing.
"""
import csv
import glob
import os
import sys

COLLECTIVES = ["all_reduce", "reduce_scatter", "all_gather", "sendrecv",
               "broadcast", "reduce", "alltoall"]


def read_curve(path):
    out = []
    with open(path) as f:
        for r in csv.DictReader(f):
            s = (r.get("size") or "").strip()
            if not s or s.startswith("#"):
                continue
            try:
                out.append((int(s), float(r["ip_busbw"]), float(r["oop_busbw"]),
                            float(r["oop_time_us"])))
            except (ValueError, KeyError):
                pass
    return sorted(out)


def read_sweep(path):
    out = []
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                out.append((r["algo"], r["proto"], str(r["channels"]),
                            float(r["peak_busbw_GBps"])))
            except (ValueError, KeyError):
                pass
    return out


def newest_per_collective(root="results"):
    picks = {}
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        base = os.path.basename(d)
        if base.startswith("sweep-"):
            continue
        for c in COLLECTIVES:
            p = os.path.join(d, f"{c}.csv")
            if os.path.exists(p):
                picks[c] = p           # later dirs (sorted) win = newest
    return picks


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        plt = None

    sweep_csvs, curve_csvs = [], {}
    for a in args:
        if os.path.isdir(a):
            s = os.path.join(a, "summary.csv")
            if os.path.exists(s):
                sweep_csvs.append(s)
            for c in COLLECTIVES:
                p = os.path.join(a, f"{c}.csv")
                if os.path.exists(p):
                    curve_csvs[c] = p
        elif a.endswith("summary.csv"):
            sweep_csvs.append(a)
        elif a.endswith(".csv"):
            name = os.path.basename(a)[:-4]
            curve_csvs[name] = a
    if not args:
        curve_csvs = newest_per_collective()

    # ---- sweep bar charts ------------------------------------------------
    for s in sweep_csvs:
        rows = sorted(read_sweep(s), key=lambda x: x[3])
        if not rows:
            continue
        labels = [f"{a}/{p}/ch{c}" for a, p, c, _ in rows]
        vals = [v for *_, v in rows]
        print(f"\n{s}")
        for lab, v in zip(labels[::-1], vals[::-1]):
            print(f"  {lab:24s} {v:7.1f} GB/s")
        if plt:
            fig, ax = plt.subplots(figsize=(8, max(3, 0.32 * len(rows))))
            ax.barh(labels, vals, color="#4C78A8")
            ax.set_xlabel("peak busbw (GB/s)")
            ax.set_title(os.path.dirname(s))
            for i, v in enumerate(vals):
                ax.text(v, i, f" {v:.1f}", va="center", fontsize=8)
            fig.tight_layout()
            out = os.path.join(os.path.dirname(s), "sweep.png")
            fig.savefig(out, dpi=120)
            print(f"  -> {out}")

    # ---- busbw + latency curves ---------------------------------------
    if curve_csvs:
        data = {c: read_curve(p) for c, p in curve_csvs.items()}
        data = {c: v for c, v in data.items() if v}
        print("\npeak in-place busbw:")
        for c, v in data.items():
            print(f"  {c:16s} {max(x[1] for x in v):7.1f} GB/s")
        if plt and data:
            fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5))
            for c, v in data.items():
                sz = [x[0] for x in v]
                a1.semilogx([x[0] for x in v], [x[1] for x in v], "o-", ms=3, label=c)
                a2.loglog(sz, [x[3] for x in v], "o-", ms=3, label=c)
            bc = data.get("broadcast")
            if bc:
                ceil = max(x[1] for x in bc)
                a1.axhline(ceil, ls="--", c="grey", lw=1)
                a1.text(a1.get_xlim()[0], ceil, f" one-way ~{ceil:.0f}", fontsize=8, va="bottom")
            a1.set_xlabel("message size (bytes)"); a1.set_ylabel("busbw (GB/s)")
            a1.set_title("bus bandwidth vs size"); a1.grid(alpha=.3); a1.legend()
            a2.set_xlabel("message size (bytes)"); a2.set_ylabel("time (us)")
            a2.set_title("op latency vs size"); a2.grid(alpha=.3, which="both"); a2.legend()
            fig.tight_layout()
            os.makedirs("results", exist_ok=True)
            out = "results/curves.png"
            fig.savefig(out, dpi=120)
            print(f"  -> {out}")

    if not plt:
        print("\n(matplotlib not found - printed tables only. On Perlmutter: module load python)")


if __name__ == "__main__":
    main()
