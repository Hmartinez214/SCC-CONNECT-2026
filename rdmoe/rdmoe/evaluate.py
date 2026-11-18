"""Evaluate a trained run: accuracy / exact match per task, split and depth,
inference compute (algorithmic FLOPs, recurrent steps, latency, throughput,
peak memory) and routing behaviour (usage, entropy, reuse, trajectories,
halting distribution).

    python -m rdmoe.evaluate runs/<name> [--n 1000]
writes runs/<name>/eval.json
"""
import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import torch

from .data import PAD, TASKS, VOCAB, build_eval_set, collate
from .models import Net, count_params


def spearman(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if x.std() == 0 or y.std() == 0:
        return float("nan")
    rx, ry = x.argsort().argsort(), y.argsort().argsort()
    return float(np.corrcoef(rx, ry)[0, 1])


@torch.no_grad()
def run_batch(model, exs, dev):
    b = collate(exs)
    tok, tgt = b["tokens"].to(dev), b["targets"].to(dev)
    sel = tgt != -100
    with torch.autocast(dev.type, dtype=torch.bfloat16, enabled=dev.type == "cuda"):
        _, aux = model(tok, ans_mask=sel, record=True)
    pred = torch.full_like(tgt, -1)
    pred[sel] = aux["ans_logits"].argmax(-1)
    return b, tok, tgt, sel, pred, aux


@torch.no_grad()
def evaluate(run_dir, n=1000, bs=500, seed=999):
    run_dir = Path(run_dir)
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    ck = torch.load(run_dir / "ckpt.pt", map_location=dev, weights_only=False)
    cfg = ck["cfg"]
    model = Net(cfg["model"], VOCAB).to(dev).eval()
    model.load_state_dict(ck["model"])
    evset = build_eval_set(n_per=n, seed=seed)
    routed = cfg["model"]["ffn"] == "moe"

    res = {"run": run_dir.name, "params": count_params(model), "train_step": ck["step"],
           "model": cfg["model"], "splits": {}, "by_depth": {}}
    usage = torch.zeros(cfg["model"].get("n_experts", 1), device=dev)
    steps_all, steps_ans_hist = [], Counter()
    traj_by_split = defaultdict(list)
    reuse, depth_steps = [], defaultdict(lambda: ([], []))
    halt_pos = []

    for (task, split), exs in evset.items():
        acc_n = acc_d = em = 0
        flops, s_ans, per_depth = [], [], defaultdict(lambda: [0, 0, [], []])
        for i in range(0, len(exs), bs):
            chunk = exs[i:i + bs]
            b, tok, tgt, sel, pred, aux = run_batch(model, chunk, dev)
            correct = (pred == tgt) & sel
            ex_em = (correct.sum(1) == sel.sum(1))
            acc_n += correct.sum().item()
            acc_d += sel.sum().item()
            em += ex_em.sum().item()
            flops.append(aux["flops"].cpu())
            st = aux["steps"]
            ans_steps = (st * sel).sum(1) / sel.sum(1)
            s_ans.append(ans_steps.cpu())
            valid = tok != PAD
            steps_all.append(st[valid].cpu())
            for v in st[sel].long().tolist():
                steps_ans_hist[v] += 1
            for j, dpt in enumerate(b["depth"].tolist()):
                pd = per_depth[dpt]
                pd[0] += ex_em[j].item()
                pd[1] += 1
                pd[2].append(ans_steps[j].item())
                pd[3].append(aux["flops"][j].item())
                depth_steps[task][0].append(dpt)
                depth_steps[task][1].append(ans_steps[j].item())
            if routed:
                tr = aux["traj"]
                usage += torch.bincount(tr[tr >= 0], minlength=usage.numel()).float()
                first_ans = sel.float().argmax(1)
                at = tr[torch.arange(tr.shape[0]), first_ans].cpu().tolist()
                for t in at:
                    t = tuple(e for e in t if e >= 0)
                    traj_by_split[split].append(t)
                    reuse.append(1 - len(set(t)) / len(t))
                tv = tr[valid]
                steps_tok = (tv >= 0).sum(1)
                distinct = torch.tensor([len(set(r[r >= 0].tolist())) for r in tv[:2000].cpu()])
                halt_pos.append((1 - distinct.float() / steps_tok[:2000].cpu().float()).mean().item())
        f = torch.cat(flops)
        sa = torch.cat(s_ans)
        res["splits"][f"{task}/{split}"] = {
            "acc": acc_n / acc_d, "exact_match": em / len(exs),
            "gflops_per_sample": f.mean().item() / 1e9,
            "steps_ans_mean": sa.mean().item(), "steps_ans_median": sa.median().item()}
        res["by_depth"][f"{task}/{split}"] = {
            str(k): {"exact_match": v[0] / v[1], "n": v[1], "steps_ans_mean": float(np.mean(v[2])),
                     "gflops_per_sample": float(np.mean(v[3])) / 1e9}
            for k, v in sorted(per_depth.items())}

    sp = res["splits"]
    iid = [v for k, v in sp.items() if k.endswith("/iid")]
    ood = [v for k, v in sp.items() if not k.endswith("/iid")]
    sa = torch.cat(steps_all).float()
    res["summary"] = {
        "iid_exact_match": float(np.mean([v["exact_match"] for v in iid])),
        "ood_exact_match": float(np.mean([v["exact_match"] for v in ood])),
        "iid_acc": float(np.mean([v["acc"] for v in iid])),
        "ood_acc": float(np.mean([v["acc"] for v in ood])),
        "iid_gflops_per_sample": float(np.mean([v["gflops_per_sample"] for v in iid])),
        "ood_gflops_per_sample": float(np.mean([v["gflops_per_sample"] for v in ood])),
        "steps_token_mean": sa.mean().item(), "steps_token_median": sa.median().item(),
        "steps_ans_hist": dict(sorted(steps_ans_hist.items())),
        "depth_steps_spearman": {t: spearman(*v) for t, v in depth_steps.items()},
    }
    if routed:
        u = (usage / usage.sum()).cpu()
        iid_tr = set(traj_by_split["iid"])
        ood_tr = [t for s, ts in traj_by_split.items() if s != "iid" for t in ts]
        res["routing"] = {
            "usage": u.tolist(),
            "usage_entropy": float(-(u * (u + 1e-9).log()).sum()),
            "usage_entropy_max": float(np.log(len(u))),
            "experts_used_gt1pct": int((u > 0.01).sum()),
            "ans_traj_reuse_rate": float(np.mean(reuse)),        # 1 - distinct/len along trajectory
            "token_traj_reuse_rate": float(np.mean(halt_pos)),
            "ans_traj_distinct_iid": len(iid_tr),
            "ood_traj_unseen_frac": float(np.mean([t not in iid_tr for t in ood_tr])) if ood_tr else None,
            "top_ans_trajectories": Counter(traj_by_split["iid"]).most_common(8),
        }
    res["efficiency"] = efficiency(model, evset, dev)
    (run_dir / "eval.json").write_text(json.dumps(res, indent=1))
    return res


@torch.no_grad()
def efficiency(model, evset, dev, n_lat=200, bs=512, reps=20):
    if dev.type != "cuda":
        return {}
    rng = np.random.default_rng(0)
    pool = [e for (t, s), exs in evset.items() if s == "iid" for e in exs]
    order = rng.permutation(len(pool))
    # latency: batch size 1 (median over samples, after warm-up)
    for i in order[:50]:
        run_batch(model, [pool[i]], dev)
    lats = []
    for i in order[:n_lat]:
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        run_batch(model, [pool[i]], dev)
        torch.cuda.synchronize()
        lats.append(time.perf_counter() - t0)
    lat = float(np.median(lats))
    # throughput: large batches (median per-batch time after warm-up; MoE chunk
    # sizes vary per batch, so the caching allocator needs several warm-up batches)
    batches = [[pool[i] for i in rng.choice(len(pool), bs)] for _ in range(reps + 5)]
    for b in batches[:5]:
        run_batch(model, b, dev)
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    times = []
    for b in batches[5:]:
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        run_batch(model, b, dev)
        torch.cuda.synchronize()
        times.append(time.perf_counter() - t0)
    thr = bs / float(np.median(times))
    return {"latency_ms_bs1": lat * 1e3, "throughput_samples_s": thr,
            "peak_mem_mb_bs512": torch.cuda.max_memory_allocated() / 2**20}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--efficiency-only", action="store_true",
                    help="re-measure latency/throughput/memory only (run on an otherwise idle GPU)")
    a = ap.parse_args()
    for r in a.runs:
        if a.efficiency_only:
            dev = torch.device("cuda")
            ck = torch.load(Path(r) / "ckpt.pt", map_location=dev, weights_only=False)
            model = Net(ck["cfg"]["model"], VOCAB).to(dev).eval()
            model.load_state_dict(ck["model"])
            res = json.loads((Path(r) / "eval.json").read_text())
            evs = build_eval_set(n_per=200, seed=999)
            cold = efficiency(model, evs, dev)
            res["efficiency"] = efficiency(model, evs, dev)          # warm (shape caches populated)
            res["efficiency"]["throughput_cold_samples_s"] = cold["throughput_samples_s"]
            res["efficiency"]["idle_gpu"] = True
            (Path(r) / "eval.json").write_text(json.dumps(res, indent=1))
            print(r, res["efficiency"])
            continue
        res = evaluate(r, n=a.n)
        print(r, json.dumps(res["summary"]), json.dumps(res["efficiency"]))


if __name__ == "__main__":
    main()
