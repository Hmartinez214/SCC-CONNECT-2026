"""Aggregate runs/*/eval.json into markdown tables and plots.

    python -m rdmoe.report runs  ->  runs/report.md, runs/*.png
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def load(runs):
    out = {}
    for p in sorted(Path(runs).glob("*/eval.json")):
        r = json.loads(p.read_text())
        m = [json.loads(l) for l in (p.parent / "metrics.jsonl").read_text().splitlines()]
        r["train_tok_s"] = sum(x["tok_per_s"] for x in m[1:]) / max(1, len(m) - 1)
        out[p.parent.name] = r
    return out


def fmt(x, n=3):
    return "–" if x is None else f"{x:.{n}f}"


def group(R):
    g = defaultdict(list)
    for k, r in R.items():
        g[re.sub(r"_s\d+$", "", k)].append(r)
    return g


def ms(vals, n=3):
    v = np.array(vals, float)
    return f"{v.mean():.{n}f}" if len(v) == 1 else f"{v.mean():.{n}f} ± {v.std():.{n}f}"


def seed_table(R):
    G = group(R)
    out = ["| model | seeds | params (M) | iid EM | OOD EM | iid GFLOP/sample | steps/answer slot | "
           "latency bs1 (ms) | throughput warm (samples/s) |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for k, rs in G.items():
        S = [r["summary"] for r in rs]
        E = [r["efficiency"] for r in rs]
        ans = [sum(int(a) * c for a, c in s["steps_ans_hist"].items()) / sum(s["steps_ans_hist"].values()) for s in S]
        out.append(f"| {k} | {len(rs)} | {rs[0]['params'] / 1e6:.1f} | {ms([s['iid_exact_match'] for s in S])} | "
                   f"{ms([s['ood_exact_match'] for s in S])} | {ms([s['iid_gflops_per_sample'] for s in S], 2)} | "
                   f"{ms(ans, 1)} | {ms([e['latency_ms_bs1'] for e in E], 1)} | {ms([e['throughput_samples_s'] for e in E], 0)} |")
    return out


def main(runs="runs"):
    R = load(runs)
    lines = ["### Seed-aggregated summary (mean ± std over seeds)", ""] + seed_table(R) + ["", "### Per run", "",
"| run | params (M) | iid EM | OOD EM | iid GFLOP/sample | OOD GFLOP/sample | "
             "mean steps/token | latency bs1 (ms) | throughput (samples/s) | train tok/s | peak mem (MB) |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for k, r in R.items():
        s, e = r["summary"], r["efficiency"]
        lines.append(f"| {k} | {r['params'] / 1e6:.1f} | {fmt(s['iid_exact_match'])} | {fmt(s['ood_exact_match'])} | "
                     f"{fmt(s['iid_gflops_per_sample'], 2)} | {fmt(s['ood_gflops_per_sample'], 2)} | "
                     f"{fmt(s['steps_token_mean'], 2)} | {fmt(e.get('latency_ms_bs1'), 1)} | "
                     f"{e.get('throughput_samples_s', 0):.0f} | {r['train_tok_s']:.0f} | {e.get('peak_mem_mb_bs512', 0):.0f} |")
    splits = list(next(iter(R.values()))["splits"].keys())
    lines += ["", "### Exact match per task / split", "",
              "| run | " + " | ".join(splits) + " |", "|---|" + "---:|" * len(splits)]
    for k, r in R.items():
        lines.append(f"| {k} | " + " | ".join(fmt(r["splits"][s]["exact_match"], 2) for s in splits) + " |")
    lines += ["", "### Routing", "",
              "| run | usage entropy (max 2.77) | experts >1% | ans-traj reuse | token-traj reuse | "
              "distinct iid ans-trajs | OOD trajs unseen in iid | steps/answer-slot hist |",
              "|---|---:|---:|---:|---:|---:|---:|---|"]
    for k, r in R.items():
        if "routing" not in r:
            continue
        g = r["routing"]
        lines.append(f"| {k} | {g['usage_entropy']:.2f} | {g['experts_used_gt1pct']} | {g['ans_traj_reuse_rate']:.2f} | "
                     f"{g['token_traj_reuse_rate']:.2f} | {g['ans_traj_distinct_iid']} | {fmt(g['ood_traj_unseen_frac'], 2)} | "
                     f"{r['summary']['steps_ans_hist']} |")
    lines += ["", "### Spearman(depth, steps at answer slot)", "",
              "| run | chain | compose | modarith | dfa |", "|---|---:|---:|---:|---:|"]
    for k, r in R.items():
        sp = r["summary"]["depth_steps_spearman"]
        lines.append(f"| {k} | " + " | ".join(fmt(sp.get(t), 2) for t in ["chain", "compose", "modarith", "dfa"]) + " |")
    (Path(runs) / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))

    # performance vs inference compute (seed means, error bars = std)
    G = group(R)
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.6))
    for ax, key in zip(axs, ["iid", "ood"]):
        for k, rs in G.items():
            x = [r["summary"][f"{key}_gflops_per_sample"] for r in rs]
            y = [r["summary"][f"{key}_exact_match"] for r in rs]
            c = "C3" if k.startswith(("D_", "P_")) else ("C0" if k[0] in "ABC" else "0.55")
            ax.errorbar(np.mean(x), np.mean(y), yerr=np.std(y) if len(y) > 1 else None, fmt="o", c=c, ms=5, capsize=3)
            ax.annotate(f"{k} (n={len(rs)})" if len(rs) > 1 else k, (np.mean(x), np.mean(y)), fontsize=6.5,
                        xytext=(3, 3), textcoords="offset points")
        ax.set_xlabel("inference GFLOPs / sample (algorithmic)")
        ax.set_ylabel("exact match")
        ax.set_title(f"{key.upper()} splits  (red: RDMoE w/ halting, blue: baselines, grey: ablations)", fontsize=9)
        ax.grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(Path(runs) / "perf_vs_compute.png", dpi=130)

    # steps vs depth (ACT runs)
    fig, axs = plt.subplots(1, 4, figsize=(15, 3.5))
    for ax, task in zip(axs, ["chain", "compose", "modarith", "dfa"]):
        for k, r in R.items():
            if k not in ("D_rdmoe", "P_ponder", "P_ponder_s2", "P_ponder_lam0", "P_ponder_lam03", "abl8_top2"):
                continue
            pts = {}
            for split, bd in r["by_depth"].items():
                if split.startswith(task + "/") and "ood_len" not in split and "ood_comp" not in split:
                    for dpt, v in bd.items():
                        pts[int(dpt)] = (v["steps_ans_mean"], v["exact_match"])
            xs = sorted(pts)
            ax.plot(xs, [pts[x][0] for x in xs], marker="o", ms=3, label=k)
        ax.set_title(task)
        ax.set_xlabel("task depth")
        ax.set_ylabel("steps at answer slot")
        ax.grid(alpha=.3)
    axs[-1].legend(fontsize=6)
    fig.tight_layout()
    fig.savefig(Path(runs) / "steps_vs_depth.png", dpi=130)

    # accuracy vs depth
    fig, axs = plt.subplots(1, 4, figsize=(15, 3.5))
    for ax, task in zip(axs, ["chain", "compose", "modarith", "dfa"]):
        for k, r in R.items():
            if k not in ("A_transformer", "A_transformer_30k", "B_looped", "C_moe", "D_rdmoe", "P_ponder", "abl1_no_halting"):
                continue
            pts = {}
            for split, bd in r["by_depth"].items():
                if split.startswith(task + "/") and "ood_len" not in split and "ood_comp" not in split:
                    for dpt, v in bd.items():
                        pts[int(dpt)] = v["exact_match"]
            xs = sorted(pts)
            ax.plot(xs, [pts[x] for x in xs], marker="o", ms=3, label=k)
        ax.set_title(task)
        ax.set_xlabel("task depth (dashed region = OOD)")
        ax.set_ylabel("exact match")
        ax.axvspan({"chain": 4.5, "compose": 4.5, "modarith": 4.5, "dfa": 6.5}[task], 12.5, color="0.9")
        ax.grid(alpha=.3)
    axs[-1].legend(fontsize=6)
    fig.tight_layout()
    fig.savefig(Path(runs) / "acc_vs_depth.png", dpi=130)


if __name__ == "__main__":
    main(*sys.argv[1:])
