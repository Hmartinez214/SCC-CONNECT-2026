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
| `hpl_runs/<jobid>/`, `stream_runs/<jobid>/` | Per-run working directories (HPL.dat / STREAM binary). Removed after each run; leftovers are from killed runs and can be deleted. |
| `descriptions.txt` | One line per job: job id and its configuration. |
| `stream/` | The earlier random STREAM sweep (`scripts/Launchpad/stream/streamsweep.py`): `trials.jsonl`/`.csv`, build/run `logs/`. Its compatible trials seeded `stream_smac3`. |

## Run groups

| Run group | Benchmark | Status |
|---|---|---|
| `autotuning/smac3_intel_hpl_v2` | Intel HPL, full space (N up to ~329k, up to 8 ranks, MEM_POLICY) | **Current** (`scripts/Launchpad/hpl/hpl_smac3.yaml`); seeded from `smac3_intel_hpl` |
| `autotuning/smac3_intel_hpl` | Intel HPL, N ~89k–95k, grids 1x1/1x2/2x1 | Finished; best ~4138 GFLOPS |
| `autotuning/smac3_launchpad_ranks` | netlib (spack) HPL, 64/128 ranks + MPI binding | Finished; best 3171 GFLOPS |
| `autotuning/stream_smac3` | STREAM Triad (MB/s) | Seeded from `stream/`; best ~472 GB/s |
| `autotuning/superseded/smac3_launchpad_bind` | netlib HPL, binding space before RANKS existed | 1 run; superseded |
| `autotuning/invalid_socket1_only/*` | netlib HPL | **Invalid**: ran inside a tmux server pinned to socket 1, so all ranks shared 32 cores |

Plots: `python scripts/Launchpad/plot_tuning.py --run-group <group>` and `python scripts/Launchpad/plot_compare.py <group> <group> ...` (write to `plots/`).
