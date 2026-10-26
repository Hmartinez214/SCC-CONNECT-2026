# outputs/

Results written by the Launchpad tuners and benchmark scripts. Everything here is generated and gitignored except this file.

| Path | What it is |
|---|---|
| `autotuning/<run_group>/evaluations.jsonl` | **The tuning record.** One JSON line per event per run: `SUBMITTED`, then a final `COMPLETED`/`FAILED`/`TIMEOUT` with the full configuration, objective (GFLOPS or Triad MB/s), metrics and log path. Tuners replay it into SMAC on restart. |
| `autotuning/<run_group>/experiment.json` | Run-group metadata: optimizer, seed, search space, objective. |
| `autotuning/<run_group>/smac3_output/` | SMAC's scratch files (rebuilt from `evaluations.jsonl`; safe to delete). |
| `autotuning/<run_group>/.lock`, `.child_pgid` | One-controller-per-group lock and the running job's session id (for orphan cleanup). |
| `autotuning/.node.lock` | Node-wide lock: HPL and STREAM tuners never run benchmarks at the same time. |
| `autotuning/*_tmux.log` | Console logs of tuner sessions started by the tmux helpers. |
| `<run_group>/<run_group>_<jobid>.log` | Each run's full application output (HPL / STREAM stdout), parsed for the result. |
| `slurm/job_<jobid>.sh`, `.out`, `.err` | The exact script each local job ran, plus its stdout/stderr (named after Slurm conventions; no Slurm is used here). |
| `slurm/job_attempt_<N>.sh` | Script the controller rendered for attempt N (shared name across tuners; the job runs a private copy). |
| `mfc_runs/<label>/` | One `scripts/Launchpad/mfc/runmfc.sh` run each (`variants_<time>/` from `mfcvariants.sh`): stand-in `case.py`, `mfc.log`, `summary.yaml`, `result.json`; field output only with `--keep`. Seeds `mfc_smac3`. |
| `mfc_tuning_runs/<jobid>/` | The same, for each `mfc_smac3` tuner run. |
| `hpl_runs/<jobid>/`, `stream_runs/<jobid>/` | Per-run working directories (HPL.dat / STREAM binary). Removed after each run; leftovers are from killed runs and can be deleted. |
| `descriptions.txt` | One line per job: job id and its configuration. |
| `system_ab/` | Fixed-config A/B tests of system settings (`scripts/Launchpad/hpl/system_ab.sh`): `results.tsv`, per-phase logs, per-run `runs/` |
| `ngc_hpl/` | NGC GPU HPL runs (`scripts/Launchpad/hpl/ngc_hpl.sh`): `<timestamp>.log`, plus `<timestamp>.dat` when N/NB/DAT were overridden |
| `hpl_bign/` | Endless large-N Intel HPL sweep (`scripts/Launchpad/hpl/hpl_bign.sh`): `results.tsv` (GFLOPS, clocks, power per run), `runs/` (HPL.dat, hpl.log, turbostat per run), `hpl_bign_tmux.log` |
| `stream_best/` | `scripts/Launchpad/stream/runstream.sh` runs of the best STREAM config: generated job script `<timestamp>.sh` and its log |
| `stream/` | The earlier random STREAM sweep (`scripts/Launchpad/stream/streamsweep.py`): `trials.jsonl`/`.csv`, build/run `logs/`. Its compatible trials seeded `stream_smac3`. |

## Run groups

| Run group | Benchmark | Status |
|---|---|---|
| `autotuning/smac3_intel_hpl_fast` | Intel HPL, all knobs (uncore, HPL_* env), N ~89k–97k | **Current** (`scripts/Launchpad/hpl/hpl_smac3.yaml`); seeded from the small-N runs of `smac3_intel_hpl_v3` |
| `autotuning/smac3_intel_hpl_v3` | Intel HPL, all knobs, N up to ~329k | Stopped after 1 run; seeded from `smac3_intel_hpl_v2` |
| `autotuning/smac3_intel_hpl_v2` | Intel HPL, N up to ~329k, up to 8 ranks, MEM_POLICY | Finished; best 4171 GFLOPS; seeded from `smac3_intel_hpl` |
| `autotuning/smac3_intel_hpl` | Intel HPL, N ~89k–95k, grids 1x1/1x2/2x1 | Finished; best ~4138 GFLOPS |
| `autotuning/smac3_launchpad_ranks` | netlib (spack) HPL, 64/128 ranks + MPI binding | Finished; best 3171 GFLOPS |
| `autotuning/stream_smac3_v3` | STREAM Triad (MB/s), + finer thread counts, KMP_BLOCKTIME and MPI (ranks, Intel MPI pinning; OMP_THREADS = total over ranks) | Superseded by v4 |
| `autotuning/stream_smac3_v4` | STREAM Triad (MB/s), OMP_THREADS per MPI rank, ranks × threads ≤ 128 | Superseded by v5 |
| `autotuning/stream_smac3_v5` | STREAM Triad (MB/s), + UNCORE_PIN, EPB, CSTATES | **Current** (`scripts/Launchpad/stream/stream_smac3.yaml`); seeded from `stream_smac3_v4` |
| `autotuning/stream_smac3_v2` | STREAM Triad (MB/s), + uncore cap and THP mode | Stopped; best 478.7 GB/s; seeded from `stream_smac3` |
| `autotuning/stream_smac3` | STREAM Triad (MB/s) | Finished; best ~472 GB/s; seeded from `stream/` |
| `autotuning/mfc_smac3` | MFC throughput (1 / grind time), 5eq_rk3_weno3_hllc, gbpp 16, 50 steps, 2 GPUs | Finished 2026-10-22 (all 24 combinations; best grind 0.206 ns, 2.0x plain); seeded from `mfc_runs/variants_20261022-053503` |
| `autotuning/mfc_smac3_<case>` | Same space for the other 6 `cases.yaml` cases (`scripts/Launchpad/mfc/mfc_smac3_all.sh`) | Finished 2026-10-23; all 24 combinations each, 0 failures; console log `autotuning/mfc_smac3_all.log`. 4 `examples/` cases (3D_performance_test, 3D_shockdroplet, 3D_TaylorGreenVortex, 3D_rayleigh_taylor) followed on 2026-10-23. |
| `autotuning/mfc_smac3_<example>` (SMAC sweep) | MFC `examples/` with SMAC, random order (seed 1) | Stopped 2026-10-24 after 2D_lungwave; replaced by the grid sweep; log `autotuning/mfc_smac3_examples.log` |
| `autotuning/mfc_grid_<example>` | Exhaustive: all 24 combinations for each of the 192 `examples/`, random order (seed 1), one case at a time; cases whose plain run fails or exceeds 10 min are skipped (`scripts/Launchpad/mfc/mfc_grid.yaml`, tmux `mfc-grid`) | Finished 2026-10-26 06:57 UTC: 151 examples x all 24 runs, 0 failed; 41 skipped at the plain run (input files, out of memory, adaptive dt, case-file errors); 7 left out of the plots as unstable; log `autotuning/mfc_grid_examples.log` |
| `autotuning/mfc_igr_extra` | igr: IGR solver + UCX transport + RDMA + pinning (`scripts/Launchpad/mfc/mfc_igr_extra.yaml`), 150 runs | Finished: 150 runs, 0 failed; best grind 0.184 ns (Gauss-Seidel, 1 iteration, RDMA, cores) vs 0.197 with the case's own solver; numerics not yet checked |
| `autotuning/superseded/smac3_launchpad_bind` | netlib HPL, binding space before RANKS existed | 1 run; superseded |
| `autotuning/superseded/smac3_intel_hpl_v2_openmpi_failures` | Intel HPL (merged tuner's first start) | **Invalid**: mpirun was Open MPI, so 81 instant failures; re-seeded |
| `autotuning/invalid_socket1_only/*` | netlib HPL | **Invalid**: ran inside a tmux server pinned to socket 1, so all ranks shared 32 cores |

Plots: see `plots/README.md` (`scripts/Launchpad/plot_*.py`, written to `plots/hpl/` and `plots/stream/`).
