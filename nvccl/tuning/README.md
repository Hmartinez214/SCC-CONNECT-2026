# Autotuning NCCL with SMAC3 + DeepCAVE

`scripts/sweep.sh` is a fixed grid. This is the smarter version: SMAC3 does
Bayesian optimization over the NCCL env-var space, and DeepCAVE visualizes
*why* a config won (parameter importance, partial dependence, trajectory).

Search space (`tune_smac.py`): `NCCL_ALGO`, `NCCL_PROTO`, channel count,
`NCCL_BUFFSIZE`, `NCCL_NTHREADS`. Score = peak in-place busbw over a size
range, maximized.

## 1. Install (on a Perlmutter login node - has internet)

```bash
cd ~/hpc/nvccl
module load python
python -m venv .venv
source .venv/bin/activate
pip install -r tuning/requirements.txt        # smac + deps, ~2-3 min
```

## 2. Run it as a batch job (don't burn an interactive session)

```bash
# edit the #SBATCH -A / QOS lines in tuning/tune.sbatch if needed
sbatch tuning/tune.sbatch
```

80 trials x ~20 s/trial ~ 30 min per collective. Output lands in
`tuning/smac_out/<collective>_g2/`. The job prints the winning
`export NCCL_...=...` block to `tuning/tune-<jobid>.out`.

Quick interactive alternative (fewer trials, in a `salloc`):
```bash
source .venv/bin/activate
python tuning/tune_smac.py --collective all_reduce --trials 30 --beg 256M --end 8G
```

## 3. Visualize with DeepCAVE (on your laptop)

```bash
# copy results back
scp -r perlmutter.nersc.gov:~/hpc/nvccl/tuning/smac_out ./smac_out

pip install deepcave
deepcave --open        # add run -> point at smac_out/all_reduce_g2
```

Useful DeepCAVE plugins for this:
- **Cost over time** - did it converge? (raise `--trials` if still climbing)
- **Importances (fANOVA / ablation)** - which knob actually mattered. Usually
  `NCCL_PROTO` and channel count; `NCCL_ALGO` near zero for 2 GPUs.
- **Partial Dependence** - e.g. busbw vs channels at fixed proto - find the plateau.
- **Parallel Coordinates** - which regions of the space are good.

## 4. Ship the result

Put the winning `NCCL_*` exports in your run script. If different sizes want
different configs (they will - LL for tiny, Simple for huge), run separate
studies per size range and build an `NCCL_TUNER_CONFIG_FILE` (NCCL >= 2.21):

```
# collective, minbytes, maxbytes, algo, proto, channels, nNodes, nRanks
allreduce,       0,     1048576, tree, LL,     -1
allreduce, 1048577,  268435456, ring, LL128,   8
allreduce, 268435457,        0, ring, simple, 16
```
```bash
export NCCL_TUNER_CONFIG_FILE=$PWD/nccl_tuner.conf
```

## Notes

- **One trial at a time** (`n_workers=1`) - parallel benchmarks contend for the
  same 2 GPUs and give garbage.
- Infeasible combos (e.g. a proto the build rejects) score `1e3` so SMAC learns
  to avoid them; runs are validated (`-c 1`).
- `deterministic=True` assumes busbw is stable run-to-run (~1-2% noise). Set
  `False` in the Scenario to let SMAC re-evaluate promising configs.
