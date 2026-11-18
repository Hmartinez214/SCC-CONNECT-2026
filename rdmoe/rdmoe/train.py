"""Training entry point.

Single GPU:   python -m rdmoe.train --config configs/rdmoe.yaml --out runs/x
Two GPUs:     torchrun --nproc_per_node 2 -m rdmoe.train --config ... --out ...
Overrides:    --set model.router=random train.steps=2000 loss.ponder=0
Resume:       re-run the same command; it resumes from <out>/ckpt.pt if present.
"""
import argparse
import json
import math
import os
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.distributed as dist
import torch.nn.functional as F
import yaml

from .data import VOCAB, StreamDataset, build_eval_set, collate, TASKS
from .models import Net, count_params


# ---- config ----------------------------------------------------------------
def _merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path, overrides=()):
    path = Path(path)
    cfg = yaml.safe_load(path.read_text())
    if "base" in cfg:
        cfg = _merge(load_config(path.parent / cfg.pop("base")), cfg)
    for ov in overrides:
        key, val = ov.split("=", 1)
        node = cfg
        *parents, leaf = key.split(".")
        for p in parents:
            node = node.setdefault(p, {})
        v = yaml.safe_load(val)
        if isinstance(v, str):          # YAML 1.1 reads "1e-3" as a string
            try:
                v = float(v)
            except ValueError:
                pass
        node[leaf] = v
    return cfg


# ---- helpers ---------------------------------------------------------------
def seed_all(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def task_loss(aux, targets):
    logits = aux["ans_logits"].float()
    tgt = targets[targets != -100]
    if "halt_dist" in aux:          # PonderNet: expected loss over halting steps
        P, LG = aux["halt_dist"], aux["step_logits"].float()
        ce = F.cross_entropy(LG.flatten(0, 1), tgt.repeat(LG.shape[0]), reduction="none").view_as(P)
        acc = (logits.argmax(-1) == tgt).float().mean()
        return (P * ce).sum(0).mean(), acc
    loss = F.cross_entropy(logits, tgt)
    acc = (logits.argmax(-1) == tgt).float().mean()
    return loss, acc


def lr_at(step, cfg):
    t = cfg["train"]
    warm, total, lr = t["warmup"], t["steps"], t["lr"]
    if step < warm:
        return lr * (step + 1) / warm
    prog = (step - warm) / max(1, total - warm)
    return t.get("min_lr_frac", 0.1) * lr + (1 - t.get("min_lr_frac", 0.1)) * lr * 0.5 * (1 + math.cos(math.pi * prog))


def make_optimizer(model, cfg):
    decay, no_decay = [], []
    for n, p in model.named_parameters():
        (no_decay if p.ndim < 2 or "emb" in n else decay).append(p)
    return torch.optim.AdamW([{"params": decay, "weight_decay": cfg["train"]["wd"]},
                              {"params": no_decay, "weight_decay": 0.0}],
                             lr=cfg["train"]["lr"], betas=(0.9, 0.95), fused=torch.cuda.is_available())


@torch.no_grad()
def quick_eval(model, evset, device, bs=500):
    model.eval()
    res = {}
    for (task, split), exs in evset.items():
        b = collate(exs)
        tok, tgt = b["tokens"].to(device), b["targets"].to(device)
        with torch.autocast(device.type, dtype=torch.bfloat16, enabled=device.type == "cuda"):
            sel = tgt != -100
            hid, aux = model(tok, ans_mask=sel)
            pred = aux["ans_logits"].argmax(-1)
        res[f"{task}/{split}"] = (pred == tgt[sel]).float().mean().item()
        res[f"{task}/{split}_steps"] = aux["steps"][sel].mean().item()
    model.train()
    return res


# ---- training --------------------------------------------------------------
def train(cfg, out_dir, device=None, num_workers=4, log=print):
    ddp = int(os.environ.get("WORLD_SIZE", "1")) > 1
    if ddp and not dist.is_initialized():
        dist.init_process_group("nccl")
    rank = dist.get_rank() if ddp else 0
    world = dist.get_world_size() if ddp else 1
    if device is None:
        if torch.cuda.is_available():
            torch.cuda.set_device(int(os.environ.get("LOCAL_RANK", 0)))
            device = torch.device("cuda")
        else:
            device = torch.device("cpu")
    out_dir = Path(out_dir)
    if rank == 0:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "config.yaml").write_text(yaml.safe_dump(cfg))

    seed_all(cfg["seed"])
    model = Net(cfg["model"], VOCAB).to(device)
    opt = make_optimizer(model, cfg)
    T = cfg["train"]
    start, ckpt_path = 0, out_dir / "ckpt.pt"
    if ckpt_path.exists():
        ck = torch.load(ckpt_path, map_location=device, weights_only=False)
        model.load_state_dict(ck["model"])
        opt.load_state_dict(ck["opt"])
        start = ck["step"]
        torch.set_rng_state(ck["rng_cpu"])
        if device.type == "cuda" and ck.get("rng_cuda") is not None:
            torch.cuda.set_rng_state(ck["rng_cuda"])
        log(f"resumed from step {start}")
    dmodel = model
    if ddp:
        dmodel = torch.nn.parallel.DistributedDataParallel(
            model, find_unused_parameters=True)  # experts / late steps may be skipped

    per_rank = T["batch_size"] // world
    ds = StreamDataset(cfg["data"], per_rank, T["steps"], cfg["seed"], rank, start)
    dl = torch.utils.data.DataLoader(ds, batch_size=None, num_workers=num_workers,
                                     pin_memory=device.type == "cuda",
                                     persistent_workers=False)
    evset = build_eval_set(n_per=200, seed=777) if rank == 0 else None
    tb = None
    if rank == 0 and cfg.get("tensorboard", True):
        from torch.utils.tensorboard import SummaryWriter
        tb = SummaryWriter(out_dir / "tb")
    if rank == 0:
        log(f"params {count_params(model) / 1e6:.2f}M  world {world}  start {start}")

    L = cfg["loss"]
    hist, t0, tok_count = [], time.time(), 0
    mlog = open(out_dir / "metrics.jsonl", "a") if rank == 0 else None
    step = start
    for step, batch in dl:
        for g in opt.param_groups:
            g["lr"] = lr_at(step, cfg)
        tok = batch["tokens"].to(device, non_blocking=True)
        tgt = batch["targets"].to(device, non_blocking=True)
        with torch.autocast(device.type, dtype=torch.bfloat16, enabled=device.type == "cuda"):
            hid, aux = dmodel(tok, ans_mask=tgt != -100)
            ltask, acc = task_loss(aux, tgt)
        loss = ltask + L["ponder"] * aux["ponder"] + L["balance"] * aux["balance"]
        if "kl" in aux:
            loss = loss + L.get("kl", 0.0) * aux["kl"]
        opt.zero_grad(set_to_none=True)
        loss.backward()
        gnorm = torch.nn.utils.clip_grad_norm_(model.parameters(), T["clip"])
        opt.step()
        tok_count += int((tok != 0).sum())

        if rank == 0 and (step % T["log_every"] == 0 or step == T["steps"] - 1):
            valid = tok != 0
            rec = {"step": step, "loss": loss.item(), "task_loss": ltask.item(),
                   "ponder": float(aux["ponder"].detach()), "balance": float(aux["balance"].detach()),
                   "kl": float(aux["kl"].detach()) if "kl" in aux else 0.0,
                   "train_acc": acc.item(), "gnorm": gnorm.item(), "lr": lr_at(step, cfg),
                   "steps_mean": aux["steps"][valid].mean().item(),
                   "steps_ans": aux["steps"][tgt != -100].mean().item(),
                   "flops_per_sample": aux["flops"].mean().item(),
                   "tok_per_s": tok_count * world / (time.time() - t0)}
            if "usage" in aux:
                u = aux["usage"] / aux["usage"].sum()
                rec["router_entropy"] = aux["router_entropy"].item()
                rec["usage_entropy"] = -(u * (u + 1e-9).log()).sum().item()
                rec["usage_max"] = u.max().item()
            tok_count, t0 = 0, time.time()
            if step % T["eval_every"] == 0 or step == T["steps"] - 1:
                rec.update({"val/" + k: v for k, v in quick_eval(model, evset, device).items()})
            log(json.dumps({k: (round(v, 5) if isinstance(v, float) else v) for k, v in rec.items()}))
            mlog.write(json.dumps(rec) + "\n")
            mlog.flush()
            hist.append(rec)
            if tb:
                for k, v in rec.items():
                    if k != "step":
                        tb.add_scalar(k, v, step)
        stop = T.get("stop_at") is not None and step + 1 >= T["stop_at"]
        if rank == 0 and ((step + 1) % T["ckpt_every"] == 0 or step == T["steps"] - 1 or stop):
            torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "step": step + 1,
                        "cfg": cfg, "rng_cpu": torch.get_rng_state(),
                        "rng_cuda": torch.cuda.get_rng_state() if device.type == "cuda" else None},
                       ckpt_path)
        if stop:
            break
    if mlog:
        mlog.close()
    if tb:
        tb.close()
    if ddp:
        dist.barrier()
    return model, hist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--set", nargs="*", default=[])
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    cfg = load_config(a.config, a.set)
    train(cfg, a.out, num_workers=a.workers)
    if dist.is_initialized():
        dist.destroy_process_group()


if __name__ == "__main__":
    main()
