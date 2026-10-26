#!/usr/bin/env python3
"""Summarize a scripts/launchpad_sweep.sh directory: per collective, the
small-message latency floor and the large-message peak busbw (out-of-place
only, as the SCC Connect 26 rules ask) with the spread over repeats, the
transport NCCL chose, and busbw / latency vs. message size plots.

    python scripts/launchpad_summary.py outputs/nccl_launchpad/sweep_<stamp>

Writes summary.md, busbw_vs_size.png and latency_vs_size.png into the dir.
"""
import glob
import os
import re
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_nccl import parse  # noqa: E402

COLLS = ["all_reduce", "all_gather", "reduce_scatter", "broadcast"]


def runs(d, coll, kind):
    return [parse(open(p).read())["rows"]
            for p in sorted(glob.glob(os.path.join(d, f"{coll}_{kind}_r*.log")))]


def spread(xs):
    return f"{st.mean(xs):.2f} (min {min(xs):.2f}, max {max(xs):.2f}, n={len(xs)})" if xs else "n/a"


def peak_link_gbs(d):
    """Per-direction NVLink peak of GPU 0: sum of its active link rates."""
    text = open(os.path.join(d, "nvlink_status.txt")).read()
    gpu0 = text.split("GPU 1:")[0]
    rates = [float(x) for x in re.findall(r"Link \d+: ([\d.]+) GB/s", gpu0)]
    return len(rates), (rates[0] if rates else 0.0), sum(rates)


def main(d):
    nlinks, rate, peak = peak_link_gbs(d)
    lines = [f"# NCCL on {os.path.basename(d)}", "",
             f"NVLink peak (GPU 0): {nlinks} links x {rate} GB/s = **{peak:.1f} GB/s** per direction", "",
             "Out-of-place only. Latency floor = time of the smallest non-empty message from the latency runs (-n 1000); "
             "peak busbw = best size of each bandwidth run (64M-8G, -n 20). "
             "Mean (min, max) over repeats.", "",
             "| collective | latency floor (us) | peak busbw (GB/s) | at size | % of NVLink peak | transport |",
             "|---|---|---|---|---|---|"]
    curves = {}
    for c in COLLS:
        # Smallest non-empty message: all_gather / reduce_scatter round 8-16 B
        # down to 0 B (a no-op), so their floor is at 32 B.
        firsts = [next(r for r in rows if r["size"] > 0) for rows in runs(d, c, "lat") if rows]
        lat = [r["oop_time_us"] for r in firsts]
        lat_size = firsts[0]["size"] if firsts else 0
        bw_runs = runs(d, c, "bw")
        peaks = [max(rows, key=lambda r: r["oop_busbw"]) for rows in bw_runs if rows]
        best = [p["oop_busbw"] for p in peaks]
        size = max(set(p["size"] for p in peaks), key=[p["size"] for p in peaks].count) if peaks else 0
        check = os.path.join(d, f"{c}_check.log")
        tr = sorted(set(re.findall(r"via (P2P/\S+|SHM\S*|NET\S*)", open(check).read()))) if os.path.exists(check) else []
        wrong = re.search(r"Out of bounds values : (\d+)", open(check).read()) if os.path.exists(check) else None
        lines.append(f"| {c} | {spread(lat)} at {lat_size} B | {spread(best)} | {size / 2**20:.0f} MiB | "
                     f"{100 * st.mean(best) / peak:.1f}% | {', '.join(tr) or 'n/a'}"
                     f"{'' if not wrong else f' ({wrong.group(1)} wrong)'} |")
        curves[c] = runs(d, c, "sweep")
    open(os.path.join(d, "summary.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib missing; no plots")
        return
    for key, ylabel, fname, logy in [("oop_busbw", "busbw (GB/s), out-of-place", "busbw_vs_size.png", False),
                                     ("oop_time_us", "time (us), out-of-place", "latency_vs_size.png", True)]:
        fig, ax = plt.subplots(figsize=(8, 5))
        for c, reps in curves.items():
            if not reps:
                continue
            reps = [[r for r in rows if r["size"] > 0] for rows in reps]
            sizes = [r["size"] for r in reps[0]]
            vals = [[rows[i][key] for rows in reps if i < len(rows)] for i in range(len(sizes))]
            mean = [st.mean(v) for v in vals]
            ax.plot(sizes, mean, marker=".", label=c)
            ax.fill_between(sizes, [min(v) for v in vals], [max(v) for v in vals], alpha=0.2)
        if key == "oop_busbw":
            ax.axhline(peak, ls="--", c="gray", lw=1, label=f"NVLink peak {peak:.0f} GB/s")
        ax.set_xscale("log", base=2)
        if logy:
            ax.set_yscale("log")
        ax.set_xlabel("message size (bytes)")
        ax.set_ylabel(ylabel)
        ax.set_title(f"NCCL, 2x H100 NVL (NV{nlinks}); mean of {len(next(iter(curves.values())))} runs, band = min-max")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(os.path.join(d, fname), dpi=150)
        print("wrote", os.path.join(d, fname))


if __name__ == "__main__":
    main(sys.argv[1])
