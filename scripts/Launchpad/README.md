# scripts/Launchpad/

Start with `bench` (one entry point; `bench -h`):

```bash
scripts/Launchpad/bench status                      # node lock, loops, machine settings, latest results
scripts/Launchpad/bench best                        # best result per benchmark
scripts/Launchpad/bench run hpl N=90482 --dry-run   # what would run; drop --dry-run to run it
scripts/Launchpad/bench run stream OMP_THREADS=60   # override any configuration key
scripts/Launchpad/bench start stream-cont           # long runs in tmux; bench stop stream-cont
scripts/Launchpad/bench find stream_smac3_v5 3716   # every file of one tuner run
scripts/Launchpad/bench exp ab -r 3 hpl 'N=90482' 'N=90482 HUGEPAGES=2m'
```

| Path | Contents |
|---|---|
| `bench` | Front end: `run` (the runbest scripts), `start`/`stop` (tmux loops and tuners), `status`, `best`, `export`, `index`, `find`, `exp`. |
| `lib/node.sh` | Shared by the shell scripts: `KEY=VALUE` / `-h` / `--dry-run` parsing, the node lock (records who holds it), sysfs settings set with sudo and restored newest-first, per-run directories and `result.json` / `results.tsv`. |
| `lib/best.py`, `lib/tmuxrun.sh` | Best configuration from `outputs/best/*.json` as `BEST_*` shell variables (with overrides); start a command in a detached tmux session with a log. |
| `paths.py` | Where results live (`outputs/<bench>/...`, see `outputs/README.md`); used by the tuners, plots, export and index. |
| `autotune_local.py` | Shared SMAC3 tuner runtime for this node (local scheduler, node lock, resumable SMAC, prior seeding, `--check-replay`). Used by `hpl/`, `stream/` and `mfc/`. Each run group writes to `outputs/<bench>/autotuning/<group>/`, its job logs to `outputs/<bench>/<group>/` and job scripts to `outputs/<bench>/slurm/`. |
| `plot_tuning.py`, `plot_compare.py`, `plot_params.py`, `plot_hpctools.py` | Plot one run group / compare several / GFLOPS vs every variable / HPC-Tools' own plotting (`outputs/<bench>/autotuning/` -> `plots/hpl/`, `plots/stream/`). Shared palette and log reading: `plot_style.py`. |
| `export_best.py` | Write the best configurations and their performance for every benchmark to `outputs/best/` (tracked in git); `bench export`. |
| `index_outputs.py` | `outputs/index.csv`: one row per tuner and runbest run (value, status, log, job script, configuration); `--find GROUP ID` lists one run's files. `bench index` / `bench find`. |
| `migrate_outputs.py` | One-off move (2026-10-31) of `outputs/` from the flat layout to one directory per benchmark; the list of moves is `outputs/archive/MOVED.tsv`. |
| `experiments/` | Node experiments behind the README findings: interleaved A/B of any runbest settings, PL1 power limit, OS noise, memory-bandwidth ceilings. |
| `tests/` | `venv/bin/python -m pytest scripts/Launchpad/tests -q`: job settings restored on exit/error/SIGTERM, Intel HPL rank placement, result parsing, prior import, replay round trip (no benchmarks run). |
| `hpl/` | HPL autotuning (Intel and netlib HPL), system/BIOS tuning guide. |
| `stream/` | STREAM autotuning and the earlier random sweep. |
| `deps/` | Build OpenBLAS / Open MPI with HPC-Tools; install Intel oneAPI. |
| `mfc/` | MFC on the H100s: build, timed runs, build-variant matrix, answer checks, SMAC3 tuning. |
| `wrf/` | Build WRF/WPS. |

Every runbest script takes `-h`, `--dry-run` and `KEY=VALUE` arguments (script settings such as `N=`, `EXCLUDE_CPUS=`, `HUGEPAGES=`, `CASE=`, `COLLS=`, or any key of the best configuration). Everything that changes the machine (uncore, THP, EPB, C-states, huge pages, GPU clocks, MSRs) is restored on exit, including Ctrl-C, and every benchmark takes the node lock, so runs never overlap.
