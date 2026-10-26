# mfc/

Build, run, compare and tune MFC (`libs/clone/MFC`) on the two H100s, with the NVIDIA HPC SDK (25.9) and OpenACC.

```bash
scripts/Launchpad/mfc/buildmfc.sh --clean                          # first build / after changing MFC_CUDA_CC
scripts/Launchpad/mfc/runmfc.sh 5eq_rk3_weno3_hllc --steps 50     # one timed run
scripts/Launchpad/mfc/mfcvariants.sh --run --steps 50              # build + time the variant matrix
python3 scripts/Launchpad/mfc/mfccheck.py summary                  # table of all runs, fastest first
scripts/Launchpad/mfc/mfc_smac3_tmux.sh                           # SMAC3 tuning in tmux 'mfc-smac3' (resumable)
python scripts/Launchpad/plot_tuning.py --run-group mfc_smac3 --metric throughput --unit "G gp·eq·rhs/s" --output plots/mfc/mfc_smac3_progress.png
python scripts/Launchpad/mfc/plot_mfc_params.py                  # effect of each variable -> plots/mfc/params/
```

| File | What it does |
|---|---|
| `buildmfc.sh [--clean] [-j N] [-- args]` | Installs missing apt packages / the SDK and builds MFC with `--gpu acc`. |
| `mfcenv.sh` | Environment (`source` it): SDK 25.9 compilers, its HPC-X MPI, `MFC_CUDA_CC=90`. |
| `runmfc.sh [opts] CASE [-- case args]` | Builds the chosen variant if needed and runs one case in `outputs/mfc_runs/<label>/`. Options: `-n`, `--case-opt`, `--env K=V`, `--fastmath`, `--precision double\|mixed\|single`, `--rdma`, `--bind none\|numa\|cores`, `--gbpp`, `--steps`, `--param K=V`, `--keep`, `--build-only`. Writes `result.json` and prints `MFC_RESULT {json}`. |
| `mfcvariants.sh [opts] [CASE]` | Builds (and with `--run`, times) every variant in `--variants` x case-opt off/on, so the tuner never compiles inside its time limit. |
| `mfccheck.py compare REF RUN` | Checks a faster variant against a reference run's `D/` output, using MFC's test-suite tolerances (exit 0 = OK). Both runs need `--keep` and the same case, `-n`, `--gbpp` and `--steps`. |
| `mfccheck.py summary [DIR]` | Lists runs by grind time, with speedup over the plain (double, no case-opt) run of the same problem. |
| `mfc_autotune.py`, `mfc_smac3.yaml`, `mfc_smac3.sh`, `mfc_smac3_tmux.sh`, `mfc_smac3_all.sh` | SMAC3 tuning (shared `../autotune_local.py` runtime and node lock) over case-opt / fastmath / RDMA MPI / binding, maximizing throughput = 1 / grind time. `mfc_autotune.py --case SLUG` tunes another case (run group `mfc_smac3_<slug>`); `mfc_smac3_all.sh` does every case in turn (`--examples --shuffle SEED --prune-builds`: all of MFC's `examples/` in random order, deleting each case's builds afterwards). |
| `mfc_grid.yaml` | Exhaustive mode (`experiment.optimizer: grid`): all 24 combinations per case in a seeded random order, run group `mfc_grid_<slug>`. `CONFIG=scripts/Launchpad/mfc/mfc_grid.yaml mfc_smac3_all.sh --examples --shuffle 1 --prune-builds` runs it over every example, one case to completion at a time. |
| `mfc_igr_extra.yaml` | Run group `mfc_igr_extra`: igr with case-opt on, tuning the IGR solver (`IGR_ITER_SOLVER`, `NUM_IGR_ITERS`), the HPC-X UCX transport (`UCX_TLS`, `UCX_RNDV_THRESH`, `UCX_RNDV_SCHEME`), RDMA and pinning; 150 runs. Runs in 30-min slots between the examples sweep's cases (`BETWEEN=` hook of `mfc_smac3_all.sh`). The IGR settings change the numerics: check the winner with `mfccheck.py compare`. |
| `plot_mfc_params.py` | One plot per tuned variable (plus `all_variables.png`) in `plots/mfc/params/`: each run's speedup over the same settings with that variable at its default, per case. |
| `cases.yaml` | Short names for cases (MFC's `benchmarks/`, a few `examples/`); add the competition case here. Any `examples/` directory name also works as a case; those get their run length from `t_step_*` overrides (`steps: param`). |
| `launchpad.mako`, `launchpad_bind.sh` | MFC job template (`mpirun` + a per-rank wrapper that pins each rank by `MFC_BIND`; both GPUs are on NUMA node 0) and the helper `mfclib.py` used by the scripts above. |

Notes:

- **Grind time** (ns per grid point per equation per RHS evaluation, lower is better) is MFC's own speed metric. The benchmark cases size the grid from `--gbpp` × ranks, so keep `--gbpp`, `--steps` and `-n` the same when comparing runs.
- **Check answers before keeping a variant**: the tuner only measures speed. `--fastmath`, `--precision mixed`/`single` change the numerics. Run a small reference and candidate with `--keep --steps 20 --param m=127 --param n=63 --param p=63` (about 2 GB of text output each; even `--gbpp 1` writes about 4 GB), then `mfccheck.py compare`. Delete their `D/` afterwards. On 5eq_rk3_weno3_hllc, `--case-opt --fastmath` matched double exactly to the 14 printed decimals; `--precision single` failed (pressure off by up to 0.15%).
- MFC remembers its last config in `libs/clone/MFC/build/lock.yaml`. `runmfc.sh` passes every flag explicitly and restores the file afterwards; other `./mfc.sh` commands still use whatever is in it.
- `MFC_CUDA_CC` is read when a build directory is first configured, so builds made before it was set need `buildmfc.sh --clean` (or `mfcvariants.sh --clean`).
- The GPUs must be free: anything else holding GPU memory (e.g. a vLLM server) makes runs fail or skews timings.
