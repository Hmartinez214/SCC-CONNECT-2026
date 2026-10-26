# HPL autotuning (SMAC3)

Tunes HPL with HPC-Tools' SMAC3 adapter. The objective is GFLOPS, and a run only counts if the residual check passes.

- **`hpl_smac3.yaml` (default): Intel's Distribution for LINPACK** (`xhpl_intel64_dynamic` from spack MKL's `mp_linpack`). Space: HPL.dat params, N ~89k–97k (fast sweeps, ~2–3 min per run), NB 256–2048, `GRID` up to 8 ranks (`HPL_HOST_NODE` / `HPL_HOST_CORE`), `MEM_POLICY`, `UNCORE_MAX_MHZ`, and the Intel env knobs below. Fixed: `HPL_LOG=2`. Run group `smac3_intel_hpl_fast`; lineage in the config header. Confirm winners at full N with `system_ab.sh -n <N>`.
- **`hpl_netlib_smac3.yaml`: spack's netlib HPL** (`hpl/zqzkvyu`: gcc + MKL + Intel MPI). Space: HPL.dat params, N, `RANKS` (64/128), `MPI_BIND`, `MPI_MAP`, `MEM_POLICY`, `HUGEPAGES`. Run group: `smac3_launchpad_ranks`.

```bash
scripts/Launchpad/hpl/hpl_smac3_tmux.sh                                        # Intel HPL, tmux session hpl-smac3
scripts/Launchpad/hpl/hpl_smac3_tmux.sh scripts/Launchpad/hpl/hpl_netlib_smac3.yaml   # netlib HPL
tmux kill-session -t hpl-smac3                                                 # stop (resumable)
python scripts/Launchpad/plot_tuning.py --run-group smac3_intel_hpl_fast       # plots/hpl/<run_group>_progress.png
python scripts/Launchpad/plot_params.py                                        # plots/hpl/params/<VARIABLE>.png
```

- Restarts replay the log into SMAC. `hpl_autotune.py --config <yaml> --check-replay` audits that replay (no runs).
- Changing the search space needs a new `run_group`.
- Each socket is held at 250 W under HPL (~2.0–2.5 GHz). Raising the power limit from Linux has no effect. OS/BIOS tuning: [SYSTEM_TUNING.md](SYSTEM_TUNING.md).
- Other files: `hpl.sh` installs spack's oneAPI-compiled HPL (`hpl/gqvmmph`). That build fails HPL's residual check, so the tuners use `hpl/zqzkvyu` (netlib) and Intel's MKL binary instead. `hpl_smac3_tmux.sh` starts `hpl_smac3.sh` in a tmux session.

## Intel HPL launch knobs

Read once by the binary (`mkl_mplinpack_ginit`); defaults are Intel's.

| Knob | Values tuned | Effect |
|---|---|---|
| `UNCORE_MAX_MHZ` | 2500 2000 1600 1400 1200 1000 800 | Machine-wide uncore cap, set per job (sudo) and restored after. Under the 250 W limit a lower cap frees power for the cores: 1.2 GHz measured +2.8% at N=150k |
| `HPL_HT` | 0, 1 | 0: one thread per physical core. 1: all logical CPUs (pin domain and `HPL_HOST_CORE` include siblings) |
| `HPL_NUMTHREADS` | all, −1, −2, −4, ¾, ½ | Threads per rank (relative to the CPUs it owns); also changes the panel-factorization thread count |
| `HPL_PAIR` | 1 2 4 8 | Update threads work in groups of this size |
| `HPL_UMODE` | 1 2 | Update tiling; only matters with `HPL_PAIR` > 1 (PAIR=1 forces 0) |
| `HPL_SWAPWIDTH` | 64 128 192 | Row-swap piece width (192 is the max on this CPU) |
| `HPL_SWAPTHRESHOLD` | 0 25 50 75 100 | % of matrix left at which the swap algorithm switches |
| `HPL_LARGEPAGE` | −1 0 1 | −1 THP off; 0 THP; 1 hugetlbfs: the job drops caches, reserves the 2 MB pages it needs, fails fast if it can't, and restores the reservation after |
| `HPL_LOG` | fixed 2 | Most informative log level |

Not tuned: `HPL_HOST_LARGEPAGE` (same as `HPL_LARGEPAGE` without offload), `HPL_NUMPFACTS` / `HPL_SORTMODE` / `HPL_MEM_AFFINITY` (no effect), `HPL_HBM` / `HPL_MIC_*` / `HPL_PFACT_INFO` (not this hardware), `MKL_ENABLE_INSTRUCTIONS` (only forces older kernels).
- `hpl_bign.sh` / `hpl_bign_tmux.sh` (tmux session `hpl-bign`): endless large-N sweep of the tuner's best Intel HPL config (attempt 355: NB 760, GRID 1x1, uncore 1.0 GHz) through `system_ab.sh`. Each cycle runs 80/88/94/100% of N_max, which is recomputed before every run from MemAvailable minus 96 GiB (2026-10-26: N_max 345,800, an 891 GiB matrix; ~6 h per cycle). Results in `outputs/hpl_bign/results.tsv`.
- `runbest.sh` / `runbest_netlib.sh` / `runbest_ngc.sh`: run the best Intel HPL (4403.6 GFLOPS at N=324520; `N=` for another size), netlib HPL (3171.3) and NGC GPU HPL (69.15 TFLOPS) configurations, each written out in full. Logs in `outputs/runbest/`.
- `ngc_hpl.sh`: GPU HPL from NGC `nvcr.io/nvidia/hpc-benchmarks:26.02.01` on both H100 NVLs (one rank per GPU, socket-0 cores; `N=`/`NB=`/`DAT=` to override). 2026-10-26: 69.15 TFLOPS at N=136192 (34.6 per GPU, both at the 400 W cap, SM ~1.29 GHz); N=144384 gave 68.80. Uses the CDI device `management.nvidia.com/gpu=all` because `--gpus all` fails (stale `/var/run/cdi/nvidia.yaml`). Logs in `outputs/ngc_hpl/`.
- `system_ab.sh [-n N] [-u KHZ] ARM ...`: A/B one runtime system setting at a time (uncore caps, numad, NUMA balancing, Kubernetes pods, huge pages) with the tuner's best config; restores the system after every run. Results in `outputs/system_ab/`.

