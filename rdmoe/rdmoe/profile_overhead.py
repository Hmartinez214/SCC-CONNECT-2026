"""Measure routing overhead and per-step cost on the GPU.

    python -m rdmoe.profile_overhead

1. MoE layer (top-1, 16 experts) vs a dense FFN of the same per-token FLOPs,
   on the same number of tokens: forward + backward wall time.
2. Full training step time per architecture with identical input batches.
3. Host-device syncs per forward (CUDA profiler: cudaStreamSynchronize / memcpy DtoH).
"""
import time

import numpy as np
import torch

from .data import VOCAB, StreamDataset
from .models import Dense, MoE, Net
from .train import load_config


def timeit(fn, n=30, warm=5):
    for _ in range(warm):
        fn()
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    torch.cuda.synchronize()
    return (time.perf_counter() - t0) / n * 1e3


def main():
    dev = torch.device("cuda")
    torch.manual_seed(0)
    d, f, N = 512, 2048, 9000
    x = torch.randn(N, d, device=dev, requires_grad=True)
    rows = []
    for name, layer in [("dense FFN", Dense(d, f)), ("MoE top-1 x16", MoE(d, [f] * 16, 1)),
                        ("MoE top-2 x16", MoE(d, [f] * 16, 2)), ("MoE random x16", MoE(d, [f] * 16, 1, "random"))]:
        layer = layer.to(dev)

        def step():
            with torch.autocast("cuda", dtype=torch.bfloat16):
                y, _ = layer(x)
            y.float().sum().backward()
        rows.append((name, timeit(step)))
    print("## FFN layer fwd+bwd, %d tokens" % N)
    for n, t in rows:
        print(f"  {n:18s} {t:7.2f} ms  ({t / rows[0][1]:.2f}x dense)")

    cfgs = {"A_transformer": "transformer", "B_looped": "looped", "C_moe": "moe", "D_rdmoe": "rdmoe"}
    ds = StreamDataset(load_config("configs/base.yaml")["data"] | {"curriculum": "none"}, 256, 10, 0)
    batch = ds.batch(0)
    tok = batch["tokens"].to(dev)
    sel = batch["targets"].to(dev) != -100
    print("\n## full train step (fwd+bwd, batch 256, %d tokens)" % int((tok != 0).sum()))
    for name, c in cfgs.items():
        model = Net(load_config(f"configs/{c}.yaml")["model"], VOCAB).to(dev)

        def step():
            with torch.autocast("cuda", dtype=torch.bfloat16):
                _, aux = model(tok, ans_mask=sel)
            (aux["ans_logits"].float().sum() + aux["ponder"] + aux["balance"]).backward()
        t = timeit(step, n=20)
        with torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CPU,
                                                torch.profiler.ProfilerActivity.CUDA]) as prof:
            with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
                _, aux = model(tok, ans_mask=sel)
            torch.cuda.synchronize()
        ev = {e.key: e.count for e in prof.key_averages()}
        syncs = ev.get("cudaStreamSynchronize", 0) + ev.get("cudaMemcpyAsync", 0)
        kern = sum(e.count for e in prof.key_averages() if e.device_type == torch.autograd.DeviceType.CUDA)
        print(f"  {name:14s} {t:7.2f} ms/step  loop iters {aux['steps_exec']}  "
              f"fwd host-syncs/memcpys {syncs}  fwd CUDA kernels {kern}")


if __name__ == "__main__":
    main()
