# wrf/

Run and tune WRF 4.8 (ARW, em_real) on the Launchpad CPUs (2x Xeon 6548Y+, 64 cores / 128 threads).

**New here? Start with [docs/RUN-WRF.md](../../../docs/RUN-WRF.md)**, the step-by-step guide.

```bash
scripts/Launchpad/wrf/build_gnu_dmsm.sh                      # MPI+OpenMP -O3 -march build (~25 min)
python3 scripts/Launchpad/wrf/runwrf.py --exe gnu_dmsm --ranks 32 --threads 4 --tiles 4   # one timed run
scripts/Launchpad/wrf/wrf_smac3_tmux.sh                      # SMAC3 tuning in tmux 'wrf-smac3' (resumable)
python scripts/Launchpad/plot_tuning.py --run-group wrf_smac3 --metric speed --unit "x real time" --output plots/wrf/wrf_smac3_progress.png
```

| File | What it does |
|---|---|
| `runwrf.py` | Links the case into `outputs/wrf/runs/<label>/` (or `--run-dir`), rewrites `namelist.input` (run length, `nproc_x/y`, `numtiles`, history/restart off, `--param GROUP:KEY=VALUE`), runs `wrf.exe` under the system `mpirun`, prints `WRF_RESULT {json}` and writes `result.json`. Options: `--exe gnu\|gnu_dmsm\|PATH`, `--ranks`, `--threads`, `--tiles`, `--decomp auto\|wide\|tall`, `--nproc-x`, `--bind core\|socket\|numa\|none`, `--hours`, `--env`, `--prefix`, `--keep`. |
| `build_gnu_dmsm.sh` | Builds the `gnu_dmsm` binary: a copy of `libs/clone/WRF`, configure option 35 (GNU dm+sm), `-O3 -march=sapphirerapids`, system netCDF and Open MPI 4.1.6. Installs `outputs/wrf/bin/wrf_gnu_dmsm.exe`. |
| `wrf_autotune.py`, `wrf_smac3.yaml`, `wrf_smac3.sh`, `wrf_smac3_tmux.sh` | SMAC3 tuning (shared `../autotune_local.py` runtime and node lock) over build, ranks, OpenMP threads, tiles, decomposition shape, binding, uncore cap, EPB and THP; maximises `speed`. |
| `buildwrf.sh` | Build WRF with the NVIDIA HPC compilers and spack's netCDF/HDF5/pnetcdf (run after `source ./launchpad_env.sh`); CMake, into `libs/opt/WRF`. Built without nesting, so it cannot run this two-domain case. |
| `holdenandco/` | Alternative WRF/WPS build scripts (`buildwrf.sh`, `buildwps.sh`) and `wrfenv.sh`. |
| `katrina.sh` | Katrina case setup (`simulations/katrina-*`). |

See `docs/WRF-SETUP.md` for the full WRF/WPS build guide.

## Case and metric

`outputs/wrf/case/santacruz/` (git-ignored): GFS-driven, 9 km d01 + 3 km d02 nest (ratio 3), 100x100x45 each, `time_step = 45`, CONUS physics suite, from 2026-09-27 00 UTC. Benchmarks run a 3-hour forecast with no history or restart output (one wrfout at t=0 during init).

**speed** = simulated seconds / wall seconds, summed over `rsl.error.0000`'s "Timing for main" lines for d01 after the first two steps. A d01 line includes its three nested d02 steps (d01 sum 42.7 s, of which d02 is 24.4 s, in a 52.6 s run), so this is the model's integration rate with init and I/O left out.

## Builds

| BUILD | Binary | Notes |
|---|---|---|
| `gnu` | `outputs/wrf/bin/wrf_gnu.exe` | The team's first build: gfortran 13, dmpar only, `-O2 -ftree-vectorize -funroll-loops`. |
| `gnu_dmsm` | `outputs/wrf/bin/wrf_gnu_dmsm.exe` | dm+sm (OpenMP), `-O3 -march=sapphirerapids`. |

## Notes

- **Patch-size limit.** WRF needs at least 10 points per patch side, so the 100-point domains allow at most 9-10 ranks per direction (about 81-100 ranks). 128 ranks (8x16) and 4x16 / 16x4 layouts abort in namelist checks. OpenMP threads are the only way to use more cores or the hyperthreads.
- **Binding** with Open MPI 4.1: threads > 1 needs `--map-by slot:PE=N` (`core:PE=N` is rejected); `runwrf.py` builds this. Above 64 ranks x threads it switches to `--use-hwthread-cpus`.
- **Not answer-checked.** The tuner only times runs. Builds and namelist changes are numerics-neutral apart from `-O3 -march` (expect bit-level differences, not physics changes).

## Results (wrf_smac3, 74 runs, 2026-11-06)

Best: **298.5x real time** (`outputs/best/wrf.json`; `runbest.sh` reproduced 299.0x), against **248x** for the team's first build at 64 MPI ranks (+20%).

```
BUILD=gnu_dmsm RANKS=60 THREADS=2 TILES=1 DECOMP=auto (6x10) BIND=socket   (UNCORE/EPB/THP: no effect)
mpirun -np 60 --use-hwthread-cpus --map-by socket:PE=2 --bind-to hwthread  -> each rank owns one core's two hyperthreads
```

What mattered (best speed seen for each value):

| Setting | Effect |
|---|---|
| Build | gnu_dmsm 298 vs gnu 270. `-O3 -march` alone (64 x 1) is +4%; OpenMP gives the rest. |
| Threads per rank | 2 (one core's two hyperthreads) 298, 1: 289, 3-8: 198-267. Hyperthreads help; wider OpenMP does not. |
| Binding | socket 298, core 296, numa 266; none fails or times out with threads (oversubscribed). |
| Decomposition | WRF's own (auto) 298, tall 293, wide 252. |
| Tiles | 1-2 best; 8-16 cost 5-25%. |
| Uncore cap, EPB, THP | Within noise (uncore 1.6/2.0/2.5 GHz all reach 298). |

The tuner was stopped at 74 of 150 runs: the best had not moved more than 0.1% in its last 35 runs. `wrf_smac3.sh` resumes it.
