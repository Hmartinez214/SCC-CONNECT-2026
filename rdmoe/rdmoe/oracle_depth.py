"""Oracle halting: apply the output head after every recurrent step and find, per
answer slot, the earliest step from which the prediction is correct and stays
correct. Shows whether *any* halting rule could use fewer steps on easy inputs.

    python -m rdmoe.oracle_depth runs/<name> [...]
"""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

from .data import VOCAB, build_eval_set, collate
from .models import Net


@torch.no_grad()
def main(runs):
    dev = torch.device("cuda")
    ev = build_eval_set(n_per=500, seed=999)
    for r in runs:
        ck = torch.load(Path(r) / "ckpt.pt", map_location=dev, weights_only=False)
        model = Net(ck["cfg"]["model"], VOCAB).to(dev).eval()
        model.load_state_dict(ck["model"])
        print(f"\n## {Path(r).name}   (oracle step | halting steps used | final acc)")
        for task in ["chain", "compose", "modarith", "dfa"]:
            rows = defaultdict(lambda: [[], [], []])
            for split in ["iid", "ood_depth"]:
                b = collate(ev[(task, split)])
                tok, tgt = b["tokens"].to(dev), b["targets"].to(dev)
                sel = tgt != -100
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    _, aux = model(tok, ans_mask=sel, record=True)
                ok = (aux["step_logits"].argmax(-1) == tgt[sel][None])      # (S, n)
                S = ok.shape[0]
                # earliest t such that ok[t:] all true (S+1 if never stably correct)
                stay = torch.flip(torch.cumprod(torch.flip(ok.int(), [0]), 0), [0]).bool()
                first = torch.where(stay.any(0), stay.float().argmax(0) + 1, torch.full_like(stay[0], S + 1, dtype=torch.long).float())
                used = aux["steps"][sel]
                for d, f, u, c in zip(b["depth"].tolist(), first.tolist(), used.tolist(), ok[-1].tolist()):
                    rows[d][0].append(f); rows[d][1].append(u); rows[d][2].append(c)
            line = []
            for d in sorted(rows):
                f, u, c = (np.array(x, float) for x in rows[d])
                fc = f[f <= S]
                line.append(f"d{d}: {np.median(fc) if len(fc) else float('nan'):.0f}|{u.mean():.1f}|{c.mean():.2f}")
            print(f"  {task:9s} " + "  ".join(line))


if __name__ == "__main__":
    main(sys.argv[1:])
