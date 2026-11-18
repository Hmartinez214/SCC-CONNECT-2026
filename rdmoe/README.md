# RDMoE — Recurrent Dynamic Mixture-of-Experts (research prototype)

Self-contained PyTorch prototype that tests one hypothesis:

> Does allowing a recurrent controller to construct input-dependent expert
> trajectories improve reasoning performance or computational efficiency over
> fixed-depth Transformer and MoE architectures?

The architecture is treated as a hypothesis, not a result. See
[REPORT.md](REPORT.md) for the first experiments, including where it does *not* win.

This directory is independent of the rest of the SCC repository: own venv,
nothing outside `rdmoe/` is read or modified.

## Layout

| Path | Contents |
|---|---|
| `rdmoe/models.py` | Attention core, dense FFN, MoE layer (router + experts + dispatch), ACT halting, `Net` (all four architectures) |
| `rdmoe/data.py` | Synthetic task generators, curriculum, deterministic streaming dataset, fixed eval sets |
| `rdmoe/train.py` | Training loop (BF16, DDP, resume, clipping, TensorBoard, JSONL metrics) |
| `rdmoe/evaluate.py` | Accuracy / exact match / compute / routing evaluation → `eval.json` |
| `rdmoe/report.py` | Aggregates runs into `runs/report.md` and plots |
| `rdmoe/profile_overhead.py` | Routing / dispatch overhead and host-sync profiling |
| `rdmoe/oracle_depth.py` | Oracle halting analysis: earliest step at which each answer is (stably) correct |
| `configs/rdmoe_ponder.yaml` | D with PonderNet halting instead of ACT |
| `results/` | Tracked copies of the report tables, plots and profile (`runs/` is gitignored) |
| `configs/` | `base.yaml` (shared) + one file per architecture |
| `experiments.txt`, `run_experiments.sh` | Experiment matrix (baselines + ablations) and a 2-GPU job runner |
| `tests/` | Unit tests (routing, halting, reuse, gradients, padding, checkpoint resume, data) |

## Architecture

All four models share one implementation. Two switches pick the model: whether
weights are shared across steps, and whether the FFN is dense or a mixture of
experts.

|                       | dense FFN                  | mixture of experts |
|---|---|---|
| distinct layers       | **A** fixed-depth Transformer | **C** conventional layer-wise MoE |
| one shared, recurrent | **B** looped Transformer      | **D** RDMoE (proposed) |

**D, one recurrent step t** (pre-norm, per token):

```
h_0     = Embedding(x) + sinusoidal PE
z_t     = h_t + Attn(LN(h_t + e_t))              shared attention core; e_t = learned step embedding
pi_t    = softmax(W_r LN(z_t))                   router over N experts
a_t     = top-k(pi_t)                            k = 1 by default
h_{t+1} = z_t + sum_k g_k * E_{a_t,k}(LN(z_t))   g = pi (top-1) or renormalised pi (top-k)
p_t     = sigmoid(W_h LN(z_t))                   halting probability
```

* **Expert reuse:** the same expert pool is visible at every step, so a token can revisit any expert.
  Routing depends on `z_t`, so trajectories differ by input and by step.
  The step embedding lets the router tell steps apart even if `z_t` barely changes.
* **Halting (ACT, Graves 2016 / Universal Transformer style):** a token halts once its cumulative
  `p` would exceed `1 − ε`, and its final step gets the remainder `R`.
  * The output is the halting-weighted mixture of per-step states.
  * A halted token's state is frozen, but other tokens can still attend to it.
  * The loop exits early when every token in the batch has halted.
  * Compute penalty: `L_compute = λ · mean_tokens(N + R)`, which is differentiable through `R`.
  * Against collapse to 1 step or to T_max steps: the halting bias is initialised to −1.5
    (≈ 6 steps at init), and the ponder coefficient is small.
* **PonderNet halting (`model.halting=ponder`, Banino et al. 2021):** halting is decided at the answer slots.
  * Each sample runs until all its answer slots halt.
  * Training uses the exact expected loss Σₜ pₜ·CEₜ over all steps (no sampling), plus
    `loss.ponder · E[T]` and `loss.kl · KL(p ‖ truncated geometric(ponder_prior))`.
  * Inference stops at the median of the halting distribution, and its predictions are identical
    to the training-time per-step predictions (unit-tested).
* **Load balancing:** Switch-style `N · Σ_i f_i · P_i`, averaged over steps or layers.
* **Losses:** `L = L_task + λ·L_compute + α·L_balance`. Each component is logged separately.
* **Experts may be heterogeneous:** set `model.expert_dims: [1024, 2048, ...]`.

Dispatch sorts the tokens by expert and runs each expert on its contiguous slice.
That costs one host sync per step for the split sizes, plus one sync per step for the halting check.

### Matching (8 steps / layers, d=512, 8 heads, d_ff=2048)

| Model | Params | Per-token compute |
|---|---:|---|
| D RDMoE (16 experts, ≤8 steps) | 34.8M | ≤ 8 × (attn + 1 expert) |
| A Transformer, 8 layers | 25.3M | = D at max depth (FLOP-matched) |
| A′ Transformer, 11 layers | 34.8M | 1.375 × D-max (param-matched) |
| B Looped, 1 block × 8 | 3.3M | = D-max (FLOP-matched, **not** param-matched) |
| C MoE, 8 layers × 2 experts top-1 | 42.1M | ≈ D-max (FLOP-matched; +21% params) |

No architecture could match both parameters and FLOPs at once, so the mismatches are listed above.
FLOPs are counted analytically, per sample, and only for tokens that are still running
(attention projections + scores, expert/FFN, router, halting head).
The embedding and the output head are excluded.

## Tasks and curriculum

All tasks are procedural. The answers are read off `ANS` slot tokens: the model is
bidirectional and nothing is decoded autoregressively. Accuracy is per slot. Exact
match requires every slot of an example to be right (assoc and attr have 2 slots).

| Stage | Task | What | Train depth | Stage-4 / OOD eval |
|---|---|---|---|---|
| 1 | `assoc` | key→value lookup among 4–8 pairs | 1 | `ood_len`: 12–16 pairs |
| 1 | `attr` | entity → (colour, shape) binding | 1 | `ood_len`: 9–12 entities |
| 2 | `chain` | k-hop pointer chasing in a random functional graph | 1–4 | `ood_depth` 5–8, `ood_len` more nodes |
| 2 | `compose` | apply k of 12 fixed global permutations | 1–4 | `ood_depth` 5–8; `ood_comp`: contains an adjacent function pair never seen in training (17% of pairs held out) |
| 3 | `modarith` | running ± sum mod 16 | 1–4 | `ood_depth` 5–8 |
| 3 | `dfa` | final state of a fixed 8-state DFA | 1–6 | `ood_depth` 7–12 |

`data.curriculum`:
* `staged`: stage 1 → 2 (depth ≤ 2) → 3 (depth ≤ 3) → everything. Earlier tasks are replayed.
* `shuffled`: exactly the same data distribution as `staged`, in random order. Each example
  draws a random "virtual step".
* `none`: the final mixture from step 0.

## Setup

```bash
cd rdmoe
python3 -m venv .venv
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cu128
.venv/bin/pip install tensorboard pyyaml pytest numpy matplotlib
```

## Train

```bash
.venv/bin/python -m rdmoe.train --config configs/rdmoe.yaml --out runs/D_rdmoe
```

Override any setting from the command line:

```bash
.venv/bin/python -m rdmoe.train --config configs/rdmoe.yaml --out runs/x --set model.router=random loss.ponder=0 train.steps=20000
```

Two GPUs with DDP (the global batch is split across ranks):

```bash
.venv/bin/torchrun --nproc_per_node 2 -m rdmoe.train --config configs/rdmoe.yaml --out runs/D_ddp
```

Re-running the same command resumes from `runs/<name>/ckpt.pt`. The data stream is
seeded by (seed, rank, step), so a resumed run sees exactly the same batches, and the
tests check that resuming reproduces the run bit for bit.

Metrics go to `runs/<name>/metrics.jsonl` and TensorBoard:

```bash
.venv/bin/tensorboard --logdir runs
```

## Evaluate, run experiments, and report

```bash
.venv/bin/python -m rdmoe.evaluate runs/D_rdmoe
```

Run the whole matrix in `experiments.txt`, 3 jobs per GPU:

```bash
./run_experiments.sh
```

Writes `runs/report.md`, `perf_vs_compute.png`, `steps_vs_depth.png` and `acc_vs_depth.png`:

```bash
.venv/bin/python -m rdmoe.report runs
```

```bash
.venv/bin/python -m rdmoe.profile_overhead
```

```bash
.venv/bin/python -m pytest -q tests
```

## Ablations (in `experiments.txt`)

| # | Ablation | Override |
|---|---|---|
| 1 | no adaptive halting (always 8 steps) | `model.halting=none` |
| 2 | no routing, fixed expert (looped + ACT) | `model.router=fixed` |
| 3 | no load balancing | `loss.balance=0` |
| 4 | random routing | `model.router=random` |
| 5 | fixed recurrent depth 4 | `model.halting=none model.n_steps=4` |
| 6 | no computation penalty | `loss.ponder=0` |
| 7 | no curriculum (shuffled / none) | `data.curriculum=shuffled` / `none` |
| 8 | top-2 routing | `model.top_k=2` |

## Not implemented yet (by design)

* **Branching/merging.**
* **The diffusion trajectory planner** (§10 of the brief). This should only be
  attempted if direct routing turns out to be useful; see REPORT.md.
* **Causal LM mode.** The tasks use answer slots, so no causal LM mode was needed.
