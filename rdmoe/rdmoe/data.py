"""Procedural synthetic tasks with controllable reasoning depth.

Every example is a token sequence. Answers are read off ``ANS`` slot tokens
(bidirectional model, no autoregressive decoding). Targets hold -100 everywhere
except at ANS positions.

Tasks (curriculum stage in brackets):
  assoc     [1] key-value lookup, 2 queries                         depth 1
  attr      [1] attribute binding (entity -> colour, shape)          depth 1
  chain     [2] pointer chasing in a functional graph, k hops        depth k
  compose   [2] composition of k fixed global permutations           depth k
  modarith  [3] running +/- sum mod 16 over k operations             depth k
  dfa       [3] final state of a fixed 8-state DFA after k inputs    depth k

Stage 4 (generalisation) is evaluation-only: longer depth (ood_depth), more
distractors / longer inputs (ood_len), and held-out adjacent function pairs
in ``compose`` (ood_comp: unseen combinations of known primitives).
"""
import numpy as np
import torch

# ---- vocabulary -----------------------------------------------------------
PAD, SEP, ANS, QRY, HOP, PLUS, MINUS, ATTR0, ATTR1 = range(9)
TASKS = ["assoc", "attr", "chain", "compose", "modarith", "dfa"]
TASK_BASE = 10                      # 10..15 task-id tokens
F_BASE, N_FUNC = 16, 12             # 16..27 function tokens for `compose`
SYM_BASE, N_SYM = 32, 64            # 32..95 generic symbols
VOCAB = 96
MOD = 16                            # alphabet for compose / modarith
DFA_STATES, DFA_INPUTS = 8, 4

# ---- fixed "world" shared by all splits (seeded constant) -------------------
_world = np.random.default_rng(12345)
PERMS = np.stack([_world.permutation(MOD) for _ in range(N_FUNC)])           # (F, MOD)
_pairs = [(i, j) for i in range(N_FUNC) for j in range(N_FUNC)]
_held = _world.choice(len(_pairs), size=len(_pairs) // 6, replace=False)
HELDOUT_PAIRS = frozenset(_pairs[k] for k in _held)                           # ~17% of pairs
DFA_TABLE = _world.integers(DFA_STATES, size=(DFA_STATES, DFA_INPUTS))         # (Q, A)


def S(i):
    return SYM_BASE + int(i)


def _wrap(task, body, query, answers):
    toks = [TASK_BASE + TASKS.index(task)] + body + [SEP] + query
    tgt = [-100] * len(toks)
    slots = [i for i, t in enumerate(toks) if t == ANS]
    assert len(slots) == len(answers)
    for i, a in zip(slots, answers):
        tgt[i] = a
    return toks, tgt


def gen_assoc(rng, depth, size, **_):
    keys = rng.choice(N_SYM, size, replace=False)
    vals = rng.integers(N_SYM, size=size)
    body = [t for k, v in zip(keys, vals) for t in (S(k), S(v))]
    q = rng.choice(size, 2, replace=False)
    query = [QRY, S(keys[q[0]]), ANS, QRY, S(keys[q[1]]), ANS]
    return _wrap("assoc", body, query, [S(vals[q[0]]), S(vals[q[1]])])


def gen_attr(rng, depth, size, **_):
    ents = rng.choice(32, size, replace=False)
    col = rng.integers(16, size=size) + 32
    shp = rng.integers(16, size=size) + 48
    body = [t for e, c, s in zip(ents, col, shp) for t in (S(e), S(c), S(s))]
    j = rng.integers(size)
    query = [QRY, S(ents[j]), ATTR0, ANS, ATTR1, ANS]
    return _wrap("attr", body, query, [S(col[j]), S(shp[j])])


def gen_chain(rng, depth, size, **_):
    nodes = rng.choice(N_SYM, size, replace=False)
    succ = rng.integers(size, size=size)
    order = rng.permutation(size)
    body = [t for i in order for t in (S(nodes[i]), S(nodes[succ[i]]))]
    x = rng.integers(size)
    cur = x
    for _ in range(depth):
        cur = succ[cur]
    query = [QRY, S(nodes[x])] + [HOP] * depth + [ANS]
    return _wrap("chain", body, query, [S(nodes[cur])])


def _sample_funcs(rng, depth, mode):
    """mode: 'train' -> no held-out adjacent pair; 'heldout' -> at least one."""
    for _ in range(1000):
        fs = rng.integers(N_FUNC, size=depth)
        has = any((fs[i], fs[i + 1]) in HELDOUT_PAIRS for i in range(depth - 1))
        if (mode == "heldout") == has:
            return fs
    raise RuntimeError("could not sample function sequence")


def compose_answer(fs, x):
    for f in fs:
        x = PERMS[f][x]
    return x


def gen_compose(rng, depth, size, mode="train", **_):
    fs = _sample_funcs(rng, depth, mode)
    x = rng.integers(MOD)
    body = [F_BASE + int(f) for f in fs] + [S(x)]
    return _wrap("compose", body, [ANS], [S(compose_answer(fs, x))])


def gen_modarith(rng, depth, size, **_):
    x = int(rng.integers(MOD))
    body = [S(x)]
    for _ in range(depth):
        a, sign = int(rng.integers(MOD)), int(rng.integers(2))
        body += [PLUS if sign == 0 else MINUS, S(a)]
        x = (x + a) % MOD if sign == 0 else (x - a) % MOD
    return _wrap("modarith", body, [ANS], [S(x)])


def dfa_answer(inputs):
    q = 0
    for a in inputs:
        q = DFA_TABLE[q, a]
    return q


def gen_dfa(rng, depth, size, **_):
    inputs = rng.integers(DFA_INPUTS, size=depth)
    body = [S(48 + int(a)) for a in inputs]
    return _wrap("dfa", body, [ANS], [S(dfa_answer(inputs))])


GEN = {"assoc": gen_assoc, "attr": gen_attr, "chain": gen_chain,
       "compose": gen_compose, "modarith": gen_modarith, "dfa": gen_dfa}

# Default training distributions: depth range and "size" (#pairs/entities/nodes).
TRAIN_SPEC = {
    "assoc":    {"depth": [1, 1], "size": [4, 8]},
    "attr":     {"depth": [1, 1], "size": [3, 6]},
    "chain":    {"depth": [1, 4], "size": [6, 10]},
    "compose":  {"depth": [1, 4], "size": [0, 0]},
    "modarith": {"depth": [1, 4], "size": [0, 0]},
    "dfa":      {"depth": [1, 6], "size": [0, 0]},
}

# Stage-4 evaluation splits (never trained on).
EVAL_SPLITS = {
    "assoc":    {"ood_len": {"depth": [1, 1], "size": [12, 16]}},
    "attr":     {"ood_len": {"depth": [1, 1], "size": [9, 12]}},
    "chain":    {"ood_depth": {"depth": [5, 8], "size": [6, 10]},
                 "ood_len": {"depth": [1, 4], "size": [14, 18]}},
    "compose":  {"ood_depth": {"depth": [5, 8], "size": [0, 0]},
                 "ood_comp": {"depth": [2, 4], "size": [0, 0], "mode": "heldout"}},
    "modarith": {"ood_depth": {"depth": [5, 8], "size": [0, 0]}},
    "dfa":      {"ood_depth": {"depth": [7, 12], "size": [0, 0]}},
}


def make_example(rng, task, spec, max_depth=None):
    lo, hi = spec["depth"]
    if max_depth is not None:
        hi = max(lo, min(hi, max_depth))
    depth = int(rng.integers(lo, hi + 1))
    slo, shi = spec["size"]
    size = int(rng.integers(slo, shi + 1))
    toks, tgt = GEN[task](rng, depth, size, mode=spec.get("mode", "train"))
    return toks, tgt, depth, TASKS.index(task)


def collate(examples, pad_multiple=8):
    L = max(len(e[0]) for e in examples)
    L = (L + pad_multiple - 1) // pad_multiple * pad_multiple
    B = len(examples)
    toks = torch.full((B, L), PAD, dtype=torch.long)
    tgt = torch.full((B, L), -100, dtype=torch.long)
    for i, (t, y, _, _) in enumerate(examples):
        toks[i, :len(t)] = torch.tensor(t)
        tgt[i, :len(y)] = torch.tensor(y)
    depth = torch.tensor([e[2] for e in examples])
    task = torch.tensor([e[3] for e in examples])
    return {"tokens": toks, "targets": tgt, "depth": depth, "task": task}


# ---- curriculum ------------------------------------------------------------
class Curriculum:
    """Maps a training step to a task mixture.

    mode='staged'   : stages in order (each stage lists tasks + depth cap).
    mode='shuffled' : the *same* multiset of data as 'staged', in random order
                      (each example draws a uniformly random virtual step).
    mode='none'     : the final stage's distribution for the whole run.
    """

    def __init__(self, cfg, total_steps):
        self.mode = cfg.get("curriculum", "staged")
        self.stages = cfg["stages"]
        self.total = total_steps
        self.spec = {k: dict(v) for k, v in TRAIN_SPEC.items()}
        for k, v in (cfg.get("train_spec") or {}).items():
            self.spec[k].update(v)

    def stage_at(self, step):
        frac = step / max(1, self.total)
        for st in self.stages:
            if frac < st["until"]:
                return st
        return self.stages[-1]

    def sample(self, rng, step):
        if self.mode == "none":
            st = self.stages[-1]
        elif self.mode == "shuffled":
            st = self.stage_at(int(rng.integers(self.total)))
        else:
            st = self.stage_at(step)
        task = st["tasks"][int(rng.integers(len(st["tasks"])))]
        return make_example(rng, task, self.spec[task], st.get("max_depth"))


class StreamDataset(torch.utils.data.IterableDataset):
    """Deterministic infinite stream: batch for step s is seeded by (seed, rank, s),
    so resuming from step s reproduces exactly the same data."""

    def __init__(self, cfg, batch_size, total_steps, seed, rank=0, start_step=0):
        self.cur = Curriculum(cfg, total_steps)
        self.bs, self.seed, self.rank = batch_size, seed, rank
        self.start, self.total = start_step, total_steps

    def batch(self, step):
        rng = np.random.default_rng([self.seed, self.rank, step])
        return collate([self.cur.sample(rng, step) for _ in range(self.bs)])

    def __iter__(self):
        info = torch.utils.data.get_worker_info()
        wid, nw = (info.id, info.num_workers) if info else (0, 1)
        for s in range(self.start + wid, self.total, nw):
            yield s, self.batch(s)


def build_eval_set(n_per=500, seed=999, tasks=TASKS, spec_override=None):
    """Fixed evaluation sets: {(task, split): [examples]}; split 'iid' uses the
    training distribution, others come from EVAL_SPLITS."""
    spec = {k: dict(v) for k, v in TRAIN_SPEC.items()}
    for k, v in (spec_override or {}).items():
        spec[k].update(v)
    out = {}
    for ti, task in enumerate(tasks):
        splits = {"iid": spec[task], **EVAL_SPLITS.get(task, {})}
        for si, (name, sp) in enumerate(splits.items()):
            rng = np.random.default_rng([seed, ti, si])
            out[(task, name)] = [make_example(rng, task, sp) for _ in range(n_per)]
    return out
