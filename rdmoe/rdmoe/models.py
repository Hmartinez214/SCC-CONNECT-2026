"""One model family covering all four architectures.

Two orthogonal switches define the baselines and the proposed model:

                     dense FFN                  mixture of experts
  distinct layers    A: fixed-depth Transformer C: conventional (layer-wise) MoE
  shared, recurrent  B: looped Transformer      D: Recurrent Dynamic MoE (RDMoE)

plus ``halting: act|none`` (adaptive computation) and router variants
(learned / random / fixed, top-k) used for ablations.

Recurrent step t (pre-norm residual):
    z_t     = h_t + Attn(LN(h_t + e_t))          e_t: optional step embedding
    a_t     = top-k of softmax(W_r LN(z_t))       per token
    h_{t+1} = z_t + sum_k g_k E_{a_t,k}(LN(z_t))
    p_t     = sigmoid(W_h LN(z_t))                halting probability (ACT)

ACT follows Graves (2016) / Universal Transformers: a token stops once its
cumulative halting probability exceeds 1 - eps; its state is then frozen (other
tokens can still attend to it). The output is the halting-weighted mixture of
per-step states and the ponder cost N + R is differentiable through R.
"""
import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from .data import ANS, PAD


def sinusoidal(n, d):
    pos = torch.arange(n).float()[:, None]
    div = torch.exp(torch.arange(0, d, 2).float() * (-math.log(10000.0) / d))
    pe = torch.zeros(n, d)
    pe[:, 0::2] = torch.sin(pos * div)
    pe[:, 1::2] = torch.cos(pos * div)
    return pe


def rope_tables(n, dh, base=10000.0):
    inv = 1.0 / base ** (torch.arange(0, dh, 2).float() / dh)
    ang = torch.arange(n).float()[:, None] * inv[None]
    return torch.cos(ang), torch.sin(ang)                      # (n, dh/2)


def apply_rope(x, cos, sin):                                     # x (B, h, L, dh)
    x1, x2 = x[..., 0::2], x[..., 1::2]
    c, s = cos[: x.shape[2]].to(x.dtype), sin[: x.shape[2]].to(x.dtype)
    return torch.stack([x1 * c - x2 * s, x1 * s + x2 * c], -1).flatten(-2)


class Attn(nn.Module):
    def __init__(self, d, heads, rope=False, max_len=256):
        super().__init__()
        self.ln = nn.LayerNorm(d)
        self.qkv = nn.Linear(d, 3 * d, bias=False)
        self.o = nn.Linear(d, d, bias=False)
        self.h = heads
        self.rope = rope
        if rope:
            c, s = rope_tables(max_len, d // heads)
            self.register_buffer("cos", c, persistent=False)
            self.register_buffer("sin", s, persistent=False)

    def forward(self, x, mask):
        B, L, D = x.shape
        q, k, v = self.qkv(self.ln(x)).view(B, L, 3, self.h, D // self.h).permute(2, 0, 3, 1, 4)
        if self.rope:
            q, k = apply_rope(q, self.cos, self.sin), apply_rope(k, self.cos, self.sin)
        y = F.scaled_dot_product_attention(q, k, v, attn_mask=mask)
        return x + self.o(y.transpose(1, 2).reshape(B, L, D))


class FFN(nn.Module):
    def __init__(self, d, f):
        super().__init__()
        self.w1, self.w2, self.f = nn.Linear(d, f), nn.Linear(f, d), f

    def forward(self, x):
        return self.w2(F.gelu(self.w1(x)))


class Dense(nn.Module):
    """Dense FFN with the same interface as MoE."""

    def __init__(self, d, f):
        super().__init__()
        self.ln, self.ffn = nn.LayerNorm(d), FFN(d, f)

    def forward(self, x):
        y = self.ffn(self.ln(x))
        n = x.shape[0]
        flops = torch.full((n,), 4.0 * x.shape[1] * self.ffn.f, device=x.device)
        return y, {"flops": flops}


class MoE(nn.Module):
    """Token-level top-k mixture of (possibly heterogeneous) FFN experts.

    router: 'learned' (softmax gate), 'random' (uniform random expert, gate 1),
            'fixed' (every token -> expert 0, gate 1).
    """

    def __init__(self, d, ff_dims, top_k=1, router="learned"):
        super().__init__()
        self.ln = nn.LayerNorm(d)
        self.experts = nn.ModuleList(FFN(d, f) for f in ff_dims)
        self.E, self.k, self.mode = len(ff_dims), top_k, router
        self.router = nn.Linear(d, self.E, bias=False)
        nn.init.normal_(self.router.weight, std=0.02)
        self.register_buffer("fdims", torch.tensor(ff_dims, dtype=torch.float), persistent=False)

    def forward(self, x):
        n, d = x.shape
        xn = self.ln(x)
        logits = self.router(xn).float()
        probs = logits.softmax(-1)
        if self.mode == "learned":
            gate, idx = probs.topk(self.k, dim=-1)
            if self.k > 1:
                gate = gate / gate.sum(-1, keepdim=True)
        elif self.mode == "random":
            idx = torch.argsort(torch.rand(n, self.E, device=x.device), -1)[:, : self.k]
            gate = torch.full_like(idx, 1.0 / self.k, dtype=torch.float)
        elif self.mode == "fixed":
            idx = torch.arange(self.k, device=x.device).expand(n, self.k)
            gate = torch.full((n, self.k), 1.0 / self.k, device=x.device)
        else:
            raise ValueError(self.mode)

        # dispatch: sort tokens by expert, one host sync for the split sizes
        flat_e = idx.reshape(-1)
        tok = torch.arange(n, device=x.device).repeat_interleave(self.k)
        order = flat_e.argsort()
        counts = torch.bincount(flat_e, minlength=self.E)
        chunks = xn[tok[order]].split(counts.tolist())
        ys = [self.experts[e](c) for e, c in enumerate(chunks) if c.shape[0] > 0]
        y = torch.cat(ys) * gate.reshape(-1)[order, None].to(ys[0].dtype)
        out = torch.zeros(n, d, dtype=y.dtype, device=x.device).index_add_(0, tok[order], y)

        # Switch-style load balancing: E * sum_i f_i P_i (1.0 when uniform)
        frac = counts.float() / max(1, flat_e.numel())
        balance = self.E * (frac * probs.mean(0)).sum() if self.mode == "learned" else x.new_zeros(())
        flops = 4.0 * d * self.fdims[idx].sum(-1) + 2.0 * d * self.E
        return out, {"idx": idx[:, 0], "probs": probs, "counts": counts, "balance": balance,
                     "flops": flops}


class Net(nn.Module):
    def __init__(self, cfg, vocab):
        super().__init__()
        d, self.cfg = cfg["d_model"], cfg
        self.shared = cfg["shared"]
        self.T = cfg["n_steps"]
        self.act = cfg.get("halting", "none") == "act"
        self.ponder = cfg.get("halting", "none") == "ponder"
        self.prior = cfg.get("ponder_prior", 0.2)              # geometric prior lambda_p
        self.eps = cfg.get("act_eps", 0.01)
        n_mod = 1 if self.shared else self.T
        self.emb = nn.Embedding(vocab, d)
        self.pos_kind = cfg.get("pos", "sinusoidal")            # sinusoidal | rope
        self.register_buffer("pos", sinusoidal(cfg.get("max_len", 256), d)
                             * (self.pos_kind == "sinusoidal"), persistent=False)
        self.step_emb = nn.Parameter(torch.zeros(self.T, d)) if cfg.get("step_embed", self.shared) else None
        self.attn = nn.ModuleList(Attn(d, cfg["n_heads"], self.pos_kind == "rope", cfg.get("max_len", 256))
                                  for _ in range(n_mod))
        if cfg["ffn"] == "dense":
            self.ffn = nn.ModuleList(Dense(d, cfg["d_ff"]) for _ in range(n_mod))
        else:
            dims = cfg.get("expert_dims") or [cfg["d_ff"]] * cfg["n_experts"]
            self.ffn = nn.ModuleList(MoE(d, dims, cfg.get("top_k", 1), cfg.get("router", "learned"))
                                     for _ in range(n_mod))
        if self.act or self.ponder:
            self.halt_ln = nn.LayerNorm(d)
            self.halt = nn.Linear(d, 1)
            nn.init.zeros_(self.halt.weight)
            nn.init.constant_(self.halt.bias, cfg.get("halt_bias_init", -1.5))
        self.ln_f = nn.LayerNorm(d)
        self.head = nn.Linear(d, vocab, bias=False)
        self.vocab = vocab

    def logits(self, h):
        return self.head(self.ln_f(h))

    def forward(self, tokens, ans_mask=None, record=False):
        """Returns final hidden states (B, L, d) and an aux dict. If ans_mask is
        given, aux['ans_logits'] holds logits at those positions (computed inside
        forward so DDP sees the head as used)."""
        B, L = tokens.shape
        dev = tokens.device
        valid = tokens != PAD
        mask = valid[:, None, None, :]
        nvalid = valid.sum(1).float()
        d = self.cfg["d_model"]
        h = self.emb(tokens) + self.pos[:L]

        running = valid.clone()
        cum = torch.zeros(B, L, device=dev)
        rem = torch.zeros(B, L, device=dev)
        n_upd = torch.zeros(B, L, device=dev)
        out = torch.zeros_like(h) if self.act else None
        flops = torch.zeros(B, device=dev)
        balance, usage, ent, probs_sum, n_routed = [], None, [], None, 0
        traj = torch.full((B, L, self.T), -1, dtype=torch.long, device=dev) if record else None
        halt_p = torch.zeros(B, L, self.T, device=dev) if record else None
        steps_exec = 0
        step_logits = []
        if self.ponder:
            # PonderNet: halting is decided at the answer slots; a sample keeps running
            # until all its answer slots have halted. Context tokens of a halted sample
            # stop too. A step's state never depends on later steps, so inference
            # (early exit) computes exactly the per-step predictions seen in training.
            ans = tokens == ANS
            ans_b = ans.nonzero(as_tuple=True)[0]
            n_ans = ans_b.numel()
            not_halted = torch.ones(n_ans, device=dev)
            cdf = torch.zeros(n_ans, device=dev)
            halted = torch.zeros(n_ans, dtype=torch.bool, device=dev)
            halt_step = torch.full((n_ans,), float(self.T), device=dev)
            sel_logits = None
            p_list, lg_list = [], []
            samp_run = torch.ones(B, dtype=torch.bool, device=dev)

        for t in range(self.T):
            if self.ponder:
                running = valid & samp_run[:, None] if not self.training else valid
            m = 0 if self.shared else t
            x = h + self.step_emb[t] if self.step_emb is not None else h
            z = self.attn[m](x, mask)
            run_idx = running.nonzero(as_tuple=True)
            y, st = self.ffn[m](z[run_idx])
            h_new = z.clone()
            h_new[run_idx] = z[run_idx] + y.to(z.dtype)

            # algorithmic FLOPs, counted only for running tokens
            attn_f = 8.0 * d * d + 4.0 * d * nvalid           # per running token, per sample
            n_run = running.sum(1).float()
            flops += n_run * attn_f
            flops.index_add_(0, run_idx[0], st["flops"].float())
            if "idx" in st:
                balance.append(st["balance"])
                usage = st["counts"].float() if usage is None else usage + st["counts"].float()
                p = st["probs"]
                ent.append(-(p * (p + 1e-9).log()).sum(-1).mean())
                probs_sum = p.sum(0) if probs_sum is None else probs_sum + p.sum(0)
                n_routed += p.shape[0]
                if record:
                    traj[run_idx[0], run_idx[1], t] = st["idx"]
            n_upd = n_upd + running.float()
            steps_exec += 1
            if record and ans_mask is not None:       # per-step answer logits (oracle-halting analysis)
                step_logits.append(self.logits(h_new[ans_mask]))

            if self.ponder:
                lg = self.logits(h_new[ans])
                lg_list.append(lg)
                lam = torch.sigmoid(self.halt(self.halt_ln(h_new[ans])).squeeze(-1).float())
                if t == self.T - 1:
                    lam = torch.ones_like(lam)
                p_t = not_halted * lam
                not_halted = not_halted * (1 - lam)
                p_list.append(p_t)
                flops.index_add_(0, ans_b, torch.full_like(p_t, 2.0 * d))
                cdf = cdf + p_t.detach()
                newly = ~halted & (cdf >= 0.5)            # deterministic: median of halting dist.
                if sel_logits is None:
                    sel_logits = torch.zeros_like(lg)
                sel_logits = torch.where(newly[:, None], lg, sel_logits)
                halt_step = torch.where(newly, torch.full_like(halt_step, t + 1), halt_step)
                halted = halted | newly
                samp_run = torch.zeros(B, dtype=torch.bool, device=dev).index_fill_(0, ans_b[~halted], True)
                h = torch.where(running[..., None], h_new, h)
                if not self.training and not bool(samp_run.any()):
                    break
            elif self.act:
                p = torch.sigmoid(self.halt(self.halt_ln(z)).squeeze(-1).float())
                if record:
                    halt_p[..., t] = p * running
                new_halt = running & ((cum + p) > 1 - self.eps)
                if t == self.T - 1:
                    new_halt = running
                still = running & ~new_halt
                r_t = (1 - cum) * new_halt
                w = p * still + r_t
                cum = cum + p * still + r_t
                rem = rem + r_t
                flops += 2.0 * d * n_run
                out = out + w[..., None] * h_new.float()
                h = torch.where(running[..., None], h_new, h)
                running = still
                if not bool(running.any()):
                    break
            else:
                h = h_new

        hid = out if self.act else h
        aux = {
            "steps": n_upd,                                     # (B, L) steps per token
            "ponder": ((n_upd + rem) * valid).sum() / valid.sum() if self.act else hid.new_zeros(()),
            "balance": torch.stack(balance).mean() if balance else hid.new_zeros(()),
            "flops": flops,                                     # (B,) per-sample algorithmic FLOPs
            "steps_exec": steps_exec,                           # loop iterations actually run
        }
        if usage is not None:
            aux["usage"] = usage
            aux["router_entropy"] = torch.stack(ent).mean()
            aux["mean_probs"] = probs_sum / max(1, n_routed)
        if record:
            aux["traj"], aux["halt_p"] = traj, halt_p
            if step_logits:
                aux["step_logits"] = torch.stack(step_logits)   # (steps_exec, n_ans, V)
        if self.ponder:
            P = torch.stack(p_list)                                   # (S, n_ans) halting distribution
            S = P.shape[0]
            tt = torch.arange(1, S + 1, device=dev, dtype=torch.float)[:, None]
            q = self.prior * (1 - self.prior) ** (tt - 1)             # truncated geometric prior
            q[-1] = 1 - q[:-1].sum()
            aux["halt_dist"] = P
            aux["step_logits"] = torch.stack(lg_list)                 # (S, n_ans, V)
            aux["ponder"] = (tt * P).sum(0).mean()                    # E[T] at answer slots
            aux["kl"] = (P * ((P + 1e-9).log() - q.log())).sum(0).mean() if S == self.T else hid.new_zeros(())
            aux["ans_logits"] = sel_logits
            steps = aux["steps"].clone()
            steps[ans] = halt_step
            aux["steps"] = steps
            return hid, aux
        if ans_mask is not None:
            aux["ans_logits"] = self.logits(hid[ans_mask])
        return hid, aux


def count_params(model):
    return sum(p.numel() for p in model.parameters())
