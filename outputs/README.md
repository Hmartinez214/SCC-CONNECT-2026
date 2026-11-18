# outputs/

Results written by the Launchpad tuners, runbest scripts and experiments. Everything here is generated and gitignored except this file and `best/`. Find things with `scripts/Launchpad/bench` (`status`, `best`, `find GROUP ID`, `index`).

## Layout

One directory per benchmark, the same shape in each:

```
outputs/
  best/                      tracked in git: best configuration + measured result per benchmark (bench export)
  index.csv                  every tuner and runbest run, one row each (bench index; regenerated, not tracked)
  .node.lock(.holder)        node-wide lock: benchmarks never overlap; the holder file says who has it
  hpl/  stream/  ngc/  mfc/  nccl/
    autotuning/<run_group>/  tuner record: evaluations.jsonl, experiment.json, smac3_output/
    autotuning/*_tmux.log    console logs of tuners started with bench start / *_tmux.sh
    <run_group>/             each tuner run's application log, <run_group>_<job_id>.log
    slurm/                   job_<job_id>.sh / .out / .err: the script each tuner job ran and its stdout/stderr
    descriptions.txt         one line per tuner job: job id and configuration
    runbest/                 runbest runs, <name>_<time>/ with result.json; results.tsv, one line per run
  experiments/<name>/        scripts/Launchpad/experiments/ (ab, pl1, fwq, bw)
  archive/                   superseded / invalid run groups, packed leftovers, MOVED.tsv (old -> new paths)
```

`bench find <run_group> <attempt|job_id>` prints one run's record, log, job script and output. The paths are defined in `scripts/Launchpad/paths.py`.

| Path | What it is |
|---|---|
| `<bench>/autotuning/<run_group>/evaluations.jsonl` | **The tuning record.** One JSON line per event per run: `SUBMITTED`, then a final `COMPLETED`/`FAILED`/`TIMEOUT` with the full configuration, objective (GFLOPS, Triad MB/s, MFC throughput), metrics and job id. Tuners replay it into SMAC on restart. Records written before 2026-10-31 hold the old absolute paths in `metadata`; `archive/MOVED.tsv` maps them. |
| `<bench>/autotuning/<run_group>/.lock`, `.child_pgid` | One-controller-per-group lock and the running job's session id (for orphan cleanup). |
| `hpl/bign/` | Endless large-N Intel HPL sweep (`hpl/hpl_bign.sh`, `bench start hpl-bign`): `results.tsv` (GFLOPS, clocks, power per run), `runs/`, `hpl_bign_tmux.log` |
| `hpl/system_ab/` | Fixed-config A/B tests of system settings (`hpl/system_ab.sh`): `results.tsv`, per-phase logs, per-run `runs/` |
| `hpl/runs/`, `stream/runs/` | Per-run working directories of tuner jobs (HPL.dat / STREAM binary), removed after each run; leftovers are from killed runs. Earlier leftovers: `archive/*_runs_leftovers.tar.zst`. |
| `stream/cont/` | Endless STREAM best-vs-variation loop (`bench start stream-cont`): `results.tsv` (Triad/Copy/Scale/Add per run), `runs.log.gz` (full log of every validated run; `zless`), `stream_cont_tmux.log`, and `runbest_stream_dirs_until_20261031.tar.zst` (the 41,915 run directories it kept before) |
| `stream/sweep/` | The earlier random STREAM sweep (`stream/streamsweep.py`): `trials.jsonl`/`.csv`, build/run `logs/`. Its compatible trials seeded `stream_smac3`. |
| `stream/runbest/legacy/` | Earlier best-STREAM runs (the old `runstream.sh`) |
| `ngc/hpl/` | NGC GPU HPL runs (`hpl/ngc_hpl.sh`): `<timestamp>.log`, plus `<timestamp>.dat` when N/NB/DAT were overridden |
| `mfc/runs/<label>/` | One `mfc/runmfc.sh` run each (`variants_<time>/` from `mfcvariants.sh`, `cases/` from `mfc_smac3_all.sh`): stand-in `case.py`, `mfc.log`, `summary.yaml`, `result.json`; field output only with `--keep`. Seeds `mfc_smac3`. |
| `mfc/tuning_runs/<job_id>/` | The same, for each MFC tuner run (12 GB: three runs kept field output). |
| `nccl/` | NCCL sweeps and tuning (`nvccl/scripts/launchpad_sweep.sh`, `launchpad_tune.py`): `sweep_*/`, `tune_*/`, console and build logs |
| `archive/` | `hpl/autotuning/{superseded,invalid_socket1_only}/` (see below) and their logs and job scripts; `slurm_job_attempt_templates.tar.zst` (the shared job_attempt_<N>.sh renders); `descriptions.txt` (before the split); `MOVED.tsv` |

## Run groups

| Run group | Benchmark | Status |
|---|---|---|
| `hpl/autotuning/smac3_intel_hpl_fast` | Intel HPL, all knobs (uncore, HPL_* env), N ~89k–97k | **Current** (`scripts/Launchpad/hpl/hpl_smac3.yaml`); seeded from the small-N runs of `smac3_intel_hpl_v3` |
| `hpl/autotuning/smac3_intel_hpl_v3` | Intel HPL, all knobs, N up to ~329k | Stopped after 1 run; seeded from `smac3_intel_hpl_v2` |
| `hpl/autotuning/smac3_intel_hpl_v2` | Intel HPL, N up to ~329k, up to 8 ranks, MEM_POLICY | Finished; best 4171 GFLOPS; seeded from `smac3_intel_hpl` |
| `hpl/autotuning/smac3_intel_hpl` | Intel HPL, N ~89k–95k, grids 1x1/1x2/2x1 | Finished; best ~4138 GFLOPS |
| `hpl/autotuning/smac3_launchpad_ranks` | netlib (spack) HPL, 64/128 ranks + MPI binding | Finished; best 3171 GFLOPS |
| `stream/autotuning/stream_smac3_v3` | STREAM Triad (MB/s), + finer thread counts, KMP_BLOCKTIME and MPI (ranks, Intel MPI pinning; OMP_THREADS = total over ranks) | Superseded by v4 |
| `stream/autotuning/stream_smac3_v4` | STREAM Triad (MB/s), OMP_THREADS per MPI rank, ranks × threads ≤ 128 | Superseded by v5 |
| `stream/autotuning/stream_smac3_v5` | STREAM Triad (MB/s), + UNCORE_PIN, EPB, CSTATES | **Current** (`scripts/Launchpad/stream/stream_smac3.yaml`); seeded from `stream_smac3_v4` |
| `stream/autotuning/stream_smac3_v2` | STREAM Triad (MB/s), + uncore cap and THP mode | Stopped; best 478.7 GB/s; seeded from `stream_smac3` |
| `stream/autotuning/stream_smac3` | STREAM Triad (MB/s) | Finished; best ~472 GB/s; seeded from `stream/` |
| `mfc/autotuning/mfc_smac3` | MFC throughput (1 / grind time), 5eq_rk3_weno3_hllc, gbpp 16, 50 steps, 2 GPUs | Finished 2026-10-22 (all 24 combinations; best grind 0.206 ns, 2.0x plain); seeded from `mfc/runs/variants_20261022-053503` |
| `mfc/autotuning/mfc_smac3_<case>` | Same space for the other 6 `cases.yaml` cases (`scripts/Launchpad/mfc/mfc_smac3_all.sh`) | Finished 2026-10-23; all 24 combinations each, 0 failures; console log `mfc/autotuning/mfc_smac3_all.log`. 4 `examples/` cases (3D_performance_test, 3D_shockdroplet, 3D_TaylorGreenVortex, 3D_rayleigh_taylor) followed on 2026-10-23. |
| `mfc/autotuning/mfc_smac3_<example>` (SMAC sweep) | MFC `examples/` with SMAC, random order (seed 1) | Stopped 2026-10-24 after 2D_lungwave; replaced by the grid sweep; log `mfc/autotuning/mfc_smac3_examples.log` |
| `mfc/autotuning/mfc_grid_<example>` | Exhaustive: all 24 combinations for each of the 192 `examples/`, random order (seed 1), one case at a time; cases whose plain run fails or exceeds 10 min are skipped (`scripts/Launchpad/mfc/mfc_grid.yaml`, tmux `mfc-grid`) | Finished 2026-10-26 06:57 UTC: 151 examples x all 24 runs, 0 failed; 41 skipped at the plain run (input files, out of memory, adaptive dt, case-file errors); 7 left out of the plots as unstable; log `mfc/autotuning/mfc_grid_examples.log` |
| `mfc/autotuning/mfc_igr_extra` | igr: IGR solver + UCX transport + RDMA + pinning (`scripts/Launchpad/mfc/mfc_igr_extra.yaml`), 150 runs | Finished: 150 runs, 0 failed; best grind 0.184 ns (Gauss-Seidel, 1 iteration, RDMA, cores) vs 0.197 with the case's own solver; numerics not yet checked |
| `archive/hpl/autotuning/superseded/smac3_launchpad_bind` | netlib HPL, binding space before RANKS existed | 1 run; superseded |
| `archive/hpl/autotuning/superseded/smac3_intel_hpl_v2_openmpi_failures` | Intel HPL (merged tuner's first start) | **Invalid**: mpirun was Open MPI, so 81 instant failures; re-seeded |
| `archive/hpl/autotuning/invalid_socket1_only/*` | netlib HPL | **Invalid**: ran inside a tmux server pinned to socket 1, so all ranks shared 32 cores |

Plots: see `plots/README.md` (`scripts/Launchpad/plot_*.py`, written to `plots/hpl/` and `plots/stream/`).
