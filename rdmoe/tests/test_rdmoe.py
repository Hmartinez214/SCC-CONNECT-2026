import copy
import json

import numpy as np
import pytest
import torch

from rdmoe import data as D
from rdmoe.models import MoE, Net
from rdmoe.train import load_config, train

CFG = {"d_model": 32, "n_heads": 4, "d_ff": 64, "max_len": 128, "shared": True, "ffn": "moe",
       "n_experts": 4, "top_k": 1, "router": "learned", "n_steps": 6, "halting": "act",
       "halt_bias_init": -1.5, "step_embed": True}


def batch(n=8, seed=0):
    ev = D.build_eval_set(n_per=2, seed=seed)
    return D.collate(sum(ev.values(), [])[:n])


def net(**kw):
    torch.manual_seed(0)
    return Net({**CFG, **kw}, D.VOCAB)


# ---- data -------------------------------------------------------------------
def test_generators_correct():
    rng = np.random.default_rng(0)
    for _ in range(50):
        fs = D._sample_funcs(rng, 3, "train")
        assert not any((fs[i], fs[i + 1]) in D.HELDOUT_PAIRS for i in range(2))
        fs = D._sample_funcs(rng, 3, "heldout")
        assert any((fs[i], fs[i + 1]) in D.HELDOUT_PAIRS for i in range(2))
    toks, tgt = D.gen_compose(rng, 3, 0)
    fs = [t - D.F_BASE for t in toks[1:4]]
    x = toks[4] - D.SYM_BASE
    assert tgt[toks.index(D.ANS)] == D.S(D.compose_answer(fs, x))
    toks, tgt = D.gen_modarith(rng, 4, 0)
    x = toks[1] - D.SYM_BASE
    for op, a in zip(toks[2:10:2], toks[3:10:2]):
        x = (x + (a - D.SYM_BASE) * (1 if op == D.PLUS else -1)) % D.MOD
    assert tgt[toks.index(D.ANS)] == D.S(x)


def test_stream_deterministic_and_resumable():
    cfg = load_config("configs/base.yaml")["data"]
    a = D.StreamDataset(cfg, 4, 100, seed=1).batch(37)
    b = list(D.StreamDataset(cfg, 4, 100, seed=1, start_step=37))[0][1]
    assert torch.equal(a["tokens"], b["tokens"])


def test_curriculum_stages():
    cfg = load_config("configs/base.yaml")["data"]
    cur = D.Curriculum(cfg, 1000)
    assert cur.stage_at(0)["tasks"] == ["assoc", "attr"]
    assert "dfa" in cur.stage_at(999)["tasks"]


# ---- routing ----------------------------------------------------------------
def test_top1_routes_to_argmax():
    torch.manual_seed(0)
    m = MoE(16, [32] * 4, top_k=1)
    x = torch.randn(50, 16)
    y, st = m(x)
    assert torch.equal(st["idx"], st["probs"].argmax(-1))
    # output equals gate * chosen expert
    i = 3
    e = st["idx"][i].item()
    ref = st["probs"][i, e] * m.experts[e](m.ln(x[i:i + 1]))
    assert torch.allclose(y[i], ref[0], atol=1e-5)


def test_top2_and_random_and_fixed():
    torch.manual_seed(0)
    x = torch.randn(400, 16)
    _, st = MoE(16, [32] * 4, top_k=2)(x)
    assert st["counts"].sum() == 800
    _, st = MoE(16, [32] * 4, router="random")(x)
    assert (st["counts"] > 50).all()                      # roughly uniform
    _, st = MoE(16, [32] * 4, router="fixed")(x)
    assert st["counts"][0] == 400 and st["counts"][1:].sum() == 0


def test_heterogeneous_experts():
    m = Net({**CFG, "expert_dims": [16, 32, 64, 128]}, D.VOCAB)
    b = batch()
    h, aux = m(b["tokens"])
    assert torch.isfinite(h).all()


def test_expert_reuse_observable():
    """Force the router to always pick expert 2: the per-token trajectory must
    revisit the same expert at every step."""
    m = net(halting="none")
    with torch.no_grad():
        m.ffn[0].router.weight.zero_()
        m.ffn[0].ln.weight.zero_()
        m.ffn[0].ln.bias.fill_(1.0)
        m.ffn[0].router.weight[2] = 1.0
    b = batch()
    _, aux = m(b["tokens"], record=True)
    tr = aux["traj"][b["tokens"] != D.PAD]
    assert (tr == 2).all() and tr.shape[-1] == CFG["n_steps"]


def test_trajectories_vary_by_step_and_input():
    m = net(halting="none")
    with torch.no_grad():
        m.step_emb.normal_(0, 3.0)
        m.ffn[0].router.weight.normal_(0, 1.0)
    b = batch(16)
    _, aux = m(b["tokens"], record=True)
    tr = aux["traj"][b["tokens"] != D.PAD]
    assert len({tuple(t.tolist()) for t in tr}) > 1               # input-dependent
    assert (tr[:, 1:] != tr[:, :-1]).any()                        # step-dependent


# ---- halting ----------------------------------------------------------------
@pytest.mark.parametrize("bias,expect", [(10.0, 1), (-10.0, CFG["n_steps"])])
def test_halting_extremes(bias, expect):
    m = net(halt_bias_init=bias)
    b = batch()
    _, aux = m(b["tokens"])
    valid = b["tokens"] != D.PAD
    assert (aux["steps"][valid] == expect).all()
    assert (aux["steps"][~valid] == 0).all()


def test_halting_variable_and_weights_sum_to_one():
    m = net()
    with torch.no_grad():
        m.halt.weight.normal_(0, 0.5)
    b = batch(16)
    _, aux = m(b["tokens"], record=True)
    valid = b["tokens"] != D.PAD
    s = aux["steps"][valid]
    assert s.min() >= 1 and s.max() <= CFG["n_steps"] and s.unique().numel() > 1
    assert aux["ponder"] >= s.mean() - 1e-4                    # N + R >= N


def test_padding_invariance():
    m = net().eval()
    b = batch(4)
    tok = b["tokens"]
    padded = torch.cat([tok, torch.zeros(tok.shape[0], 8, dtype=torch.long)], 1)
    h1, a1 = m(tok)
    h2, a2 = m(padded)
    assert torch.allclose(h1, h2[:, : tok.shape[1]], atol=1e-4)
    assert torch.equal(a1["steps"], a2["steps"][:, : tok.shape[1]])


# ---- gradients --------------------------------------------------------------
def test_gradients_reach_router_halting_attention_experts():
    m = net()
    b = batch(16)
    sel = b["targets"] != -100
    h, aux = m(b["tokens"], ans_mask=sel)
    loss = torch.nn.functional.cross_entropy(aux["ans_logits"], b["targets"][sel]) \
        + 0.01 * aux["ponder"] + 0.01 * aux["balance"]
    loss.backward()
    g = lambda p: p.grad is not None and p.grad.abs().sum() > 0
    assert g(m.ffn[0].router.weight)
    assert g(m.halt.weight) or g(m.halt.bias)
    assert g(m.attn[0].qkv.weight) and g(m.emb.weight) and g(m.step_emb)
    used = aux["usage"] > 0
    for e, u in enumerate(used.tolist()):
        assert g(m.ffn[0].experts[e].w1.weight) == u


@pytest.mark.parametrize("arch", ["transformer", "looped", "moe"])
def test_baselines_run(arch):
    cfg = load_config(f"configs/{arch}.yaml")["model"]
    cfg.update(d_model=32, n_heads=4, d_ff=64)
    m = Net(cfg, D.VOCAB)
    b = batch()
    h, aux = m(b["tokens"])
    assert (aux["steps"][b["tokens"] != D.PAD] == cfg["n_steps"]).all()


# ---- checkpoint restoration -------------------------------------------------
def _tiny_cfg(steps):
    cfg = load_config("configs/rdmoe.yaml",
                      ["model.d_model=32", "model.n_heads=4", "model.d_ff=64", "model.n_experts=4",
                       f"train.steps={steps}", "train.batch_size=16", "train.warmup=2",
                       "train.log_every=1", "train.eval_every=1000", "train.ckpt_every=3",
                       "tensorboard=false"])
    return cfg


def test_checkpoint_resume_matches_uninterrupted(tmp_path):
    cfg = _tiny_cfg(6)
    dev = torch.device("cpu")
    _, full = train(copy.deepcopy(cfg), tmp_path / "a", device=dev, num_workers=0, log=lambda *_: None)
    c2 = copy.deepcopy(cfg)
    c2["train"]["stop_at"] = 3
    train(c2, tmp_path / "b", device=dev, num_workers=0, log=lambda *_: None)
    _, resumed = train(copy.deepcopy(cfg), tmp_path / "b", device=dev, num_workers=0, log=lambda *_: None)
    la = [r["loss"] for r in full][3:]
    lb = [r["loss"] for r in resumed]
    assert len(lb) == 3 and np.allclose(la, lb, rtol=1e-5)
    sa = torch.load(tmp_path / "a" / "ckpt.pt", weights_only=False)["model"]
    sb = torch.load(tmp_path / "b" / "ckpt.pt", weights_only=False)["model"]
    assert all(torch.allclose(sa[k], sb[k], atol=1e-6) for k in sa)


# ---- PonderNet halting ------------------------------------------------------
def test_ponder_distribution_and_train_mode():
    m = net(halting="ponder").train()
    b = batch(16)
    sel = b["targets"] != -100
    _, aux = m(b["tokens"], ans_mask=sel)
    P = aux["halt_dist"]
    assert P.shape == (CFG["n_steps"], int(sel.sum()))
    assert torch.allclose(P.sum(0), torch.ones(P.shape[1]), atol=1e-5)
    assert aux["kl"] >= -1e-6 and 1 <= aux["ponder"] <= CFG["n_steps"]
    assert aux["steps_exec"] == CFG["n_steps"]                     # training runs every step


@pytest.mark.parametrize("bias,expect", [(10.0, 1), (-10.0, CFG["n_steps"])])
def test_ponder_eval_early_exit(bias, expect):
    m = net(halting="ponder", halt_bias_init=bias).eval()
    b = batch(8)
    _, aux = m(b["tokens"], ans_mask=b["targets"] != -100)
    assert aux["steps_exec"] == expect
    assert (aux["steps"][b["tokens"] == D.ANS] == expect).all()


def test_ponder_inference_matches_training_step_predictions():
    """Early exit must not change predictions: the logits at each answer slot's
    halting step equal the training-mode per-step logits at that step."""
    m = net(halting="ponder", halt_bias_init=-1.5)
    with torch.no_grad():
        m.halt.weight.normal_(0, 0.1)
    b = batch(16)
    sel = b["targets"] != -100
    m.train()
    _, tr = m(b["tokens"], ans_mask=sel)
    m.eval()
    _, ev = m(b["tokens"], ans_mask=sel)
    hs = ev["steps"][b["tokens"] == D.ANS].long() - 1
    ref = tr["step_logits"][hs, torch.arange(hs.numel())]
    assert torch.allclose(ev["ans_logits"], ref, atol=1e-4)
    assert hs.unique().numel() > 1                                 # depth actually varies


def test_ponder_gradients_reach_halting():
    from rdmoe.train import task_loss
    m = net(halting="ponder").train()
    b = batch(16)
    _, aux = m(b["tokens"], ans_mask=b["targets"] != -100)
    lt, _ = task_loss(aux, b["targets"])
    (lt + 0.01 * aux["ponder"] + 0.01 * aux["kl"]).backward()
    assert m.halt.weight.grad.abs().sum() > 0
