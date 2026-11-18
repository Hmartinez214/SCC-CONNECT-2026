# Running WRF on the Launchpad node

Step-by-step instructions for running WRF's Santa Cruz case on the Launchpad
node, from a fresh checkout through to reading the result. Run every command
from the repository root (`SCC-CONNECT-2026/`).

**The case:** a GFS-driven forecast for the Santa Cruz area starting
2026-09-27 00 UTC. It has two domains, a 9 km outer domain (d01) and a 3 km
nest inside it (d02), each 100 × 100 points with 45 levels. Input data covers
5 days.

**The score:** WRF reports **speed** = simulated time / wall time. "298×"
means one hour of weather is simulated in about 12 seconds. Higher is better.

---

## Quick version

Once the case and binaries are set up (steps 1 and 2), there are two ways to run:

```bash
scripts/Launchpad/wrf/runbest.sh
```

This runs a 3-hour forecast with the best settings found (about 1 minute), and should print `result: ~298x real time`.

```bash
python3 scripts/Launchpad/wrf/runwrf.py --exe gnu_dmsm --ranks 60 --threads 2 --bind socket
```

This runs once with settings you choose.

---

## 1. Get the case data (once)

The input files are large (about 800 MB) and kept out of git. Copy them from the
team's WPS output:

```bash
mkdir -p outputs/wrf/case/santacruz
cp /home/nvidia/SCC-CONNECT-2026/simulations/santacruz/wrf/{wrfinput_d01,wrfinput_d02,wrfbdy_d01,namelist.input,freezeH2O.dat,qr_acr_qg_V4.dat,qr_acr_qsV2.dat} \
   outputs/wrf/case/santacruz/
```

Check: `ls outputs/wrf/case/santacruz` shows those 7 files.

WRF also needs its lookup tables (`RRTMG_*`, `CAM_*`, `*.TBL`, ...). The run
scripts link them from `libs/opt/WRF/run/`. If that folder is missing, link the
source tree's copy instead:

```bash
ln -s ../clone/WRF/run libs/opt/WRF/run
```

## 2. Get a WRF binary (once)

The run scripts know two builds, both in `outputs/wrf/bin/` (also kept out of git):

| Name (`--exe`) | What it is | Speed |
|---|---|---|
| `gnu_dmsm` | MPI + OpenMP, `-O3 -march=sapphirerapids`. **Use this one.** | ~298× |
| `gnu` | The team's first build, MPI only, `-O2` | ~250× |

Check what you have:

```bash
ls -la outputs/wrf/bin/
```

If `wrf_gnu_dmsm.exe` is missing, build it (about 25 minutes, uses 48 cores):

```bash
scripts/Launchpad/wrf/build_gnu_dmsm.sh
```

It copies `libs/clone/WRF` to `outputs/wrf/build/WRF-gnu-dmsm`, so the
shared source tree is never touched, and installs `outputs/wrf/bin/wrf_gnu_dmsm.exe`.

## 3. Run the best configuration

```bash
scripts/Launchpad/wrf/runbest.sh
```

What it does:

1. Reads the best settings from `outputs/best/wrf.json`: 60 MPI ranks × 2
   OpenMP threads, socket binding, 1 tile.
2. Waits for the node lock, so it never runs at the same time as another benchmark.
3. Runs a 3-hour forecast (about 1 minute), then puts back any system settings
   it changed (THP, uncore clock, EPB). It needs passwordless `sudo` for these.

Expected end of output:

```
result: 298.99x real time (best 298.47), success yes  (run: outputs/wrf/runbest/wrf_<time>)
```

Every run adds a line to `outputs/wrf/runbest/results.tsv`.

Useful variations:

```bash
scripts/Launchpad/wrf/runbest.sh --dry-run
```

This shows the settings and runs nothing.

```bash
scripts/Launchpad/wrf/runbest.sh HOURS=24
```

This runs a longer forecast.

```bash
scripts/Launchpad/wrf/runbest.sh RANKS=32 THREADS=4
```

This overrides individual settings.

## 4. Try your own settings

`runwrf.py` runs the case once with whatever layout you give it:

```bash
python3 scripts/Launchpad/wrf/runwrf.py --exe gnu_dmsm --ranks 60 --threads 2 --bind socket --hours 3
```

| Option | Meaning | Tips |
|---|---|---|
| `--exe` | `gnu_dmsm`, `gnu`, or a path to any `wrf.exe` | `gnu` only supports `--threads 1` |
| `--ranks` | MPI ranks | **81 or fewer**: WRF needs at least 10 grid points per rank in each direction |
| `--threads` | OpenMP threads per rank | 2 is best; ranks × threads must be 128 or fewer |
| `--bind` | `socket`, `core`, `numa`, `none` | `socket` or `core`; `none` is very slow with threads |
| `--tiles` | tiles per rank | 1 or 2 |
| `--decomp` | `auto`, `wide`, `tall` | leave `auto` |
| `--hours` | forecast length | 3 is enough for timing; the data allows up to 120 |
| `--keep` | keep the `rsl.*` logs | |

The last line is `WRF_RESULT {...}`, with `"speed"` and `"success"`. The run folder
is `outputs/wrf/runs/<label>/`, and it also holds `result.json`.

These runs switch off forecast output (history and restart files) so they only
measure compute speed. For real forecast files, see step 6.

## 5. Read the result

- **Speed:** the `speed` value in `result.json` (or the `result:` line from `runbest.sh`).
- **Did it finish?** The last line of `rsl.error.0000` in the run folder should be
  `wrf: SUCCESS COMPLETE WRF`.
- **Per-step timing:** `grep "Timing for main" rsl.error.0000`. Each d01 line
  includes its three d02 steps.

## 6. A real forecast with output files

To get `wrfout_*` files, for plots or the report, run `wrf.exe` yourself in a new folder:

```bash
RUN=outputs/wrf/forecast_$(date +%Y%m%d_%H%M); mkdir -p $RUN && cd $RUN
ln -s ../../../libs/opt/WRF/run/* .
ln -sf ../case/santacruz/* .
rm namelist.input && cp ../case/santacruz/namelist.input .
```

The `wrf/run` tables are linked first, so the case's own files take priority. The namelist is copied rather than linked so it can be edited.

Edit `namelist.input` if needed. The default is the full 5 days (to 2026-10-02 00 UTC),
which takes about 25 minutes plus output time. To run a shorter forecast, change the
run length **and** the end date together. For example, for 6 hours:

```bash
sed -i 's/^ run_days .*/ run_days                 = 0,/;  s/^ run_hours .*/ run_hours                = 6,/;
        s/^ end_month .*/ end_month                = 09,   09,/; s/^ end_day .*/ end_day                  = 27,   27,/;
        s/^ end_hour .*/ end_hour                 = 06,   06,/' namelist.input
```

`history_interval` sets the minutes between output files (default 60).

Then run it with the best layout:

```bash
export OMP_NUM_THREADS=2 OMP_STACKSIZE=64M OMP_PLACES=threads OMP_PROC_BIND=close
/usr/bin/mpirun -np 60 --use-hwthread-cpus --map-by socket:PE=2 --bind-to hwthread --rank-by slot \
    -x OMP_NUM_THREADS -x OMP_STACKSIZE -x OMP_PLACES -x OMP_PROC_BIND ../bin/wrf_gnu_dmsm.exe
tail -1 rsl.error.0000
```

The last line should say `SUCCESS COMPLETE WRF`. The output files are `wrfout_d01_*` and `wrfout_d02_*` in this folder.

Use the system `/usr/bin/mpirun` (Open MPI 4.1.6), because both binaries are built
against it. Don't use HPC-X's or Intel MPI's `mpirun` for these binaries.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `rsl.error.0000` ends with an error about `nproc_x`/`nproc_y` or "patch size" | Too many ranks for a 100-point domain: use 81 or fewer (60 is best). |
| `--map-by core:PE=2` is rejected | Open MPI 4.1 needs `--map-by slot:PE=N` or `socket:PE=N`. `runwrf.py` handles this. |
| Run hangs or is very slow | Another job is using the node (check `htop`), or `--bind none` with threads. `runbest.sh` waits for `outputs/.node.lock`. |
| `error while loading shared libraries: libnetcdff.so` | The binary expects the system netCDF (`/usr/lib/x86_64-linux-gnu`). Don't run with another stack's `LD_LIBRARY_PATH` loaded, for example after `source stackenv.sh`. |
| `could not open file ... RRTMG_LW_DATA` (or another table) | The `libs/opt/WRF/run` link is missing: see step 1. |
| `runbest.sh: needs passwordless sudo` | It changes THP, uncore and EPB settings for the run. Use `runwrf.py` instead, which needs no sudo. |

## More

- `scripts/Launchpad/wrf/README.md`: how speed is measured, the tuning results,
  and what each setting does.
- `scripts/Launchpad/wrf/wrf_smac3_tmux.sh`: re-run the autotuner.
- `docs/WRF-SETUP.md`: building WRF and WPS from scratch.
