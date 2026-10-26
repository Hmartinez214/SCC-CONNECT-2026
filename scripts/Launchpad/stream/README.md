# STREAM autotuning (SMAC3)

Compiles `stream.c` with spack's icx and tunes compile flags, array size, NTIMES, OFFSET, OpenMP threading (`OMP_THREADS` per rank, 1–128 in fine steps around 64, `KMP_AFFINITY`, `OMP_PROC_BIND`, `OMP_PLACES`, `KMP_BLOCKTIME` spin-wait), numactl policy, MPI (`MPI_RANKS` 1–64, `MPI_PIN_DOMAIN`, `MPI_PIN_ORDER`), the uncore frequency cap (`UNCORE_MAX_MHZ`, and `UNCORE_PIN` to hold it there), the THP mode (`THP`: madvise / always / never), the energy-performance bias (`EPB` 6 / 4 / 0) and idle states (`CSTATES`: all / no C6 / C1 only). Objective: Triad MB/s; a run only counts if STREAM validates. Arrays are always ≥ 1 GiB, so results measure memory, not cache.

`MPI_RANKS` > 1 builds the official MPI STREAM (`libs/clone/stream_mpi/stream_mpi.c`, fetched and sha256-checked by the runner) with Intel MPI's `mpiicx` and runs it with Intel MPI's `mpirun` (shm fabric). `OMP_THREADS` is threads per rank, and `MPI_RANKS × OMP_THREADS` may not exceed the 128 logical CPUs (forbidden clauses in SMAC's space, so it never suggests such a pair). The arrays are split across ranks and allocated by each rank, and `stream_mpi.c` ignores `OFFSET`. `MPI_RANKS=1` is plain `stream.c`. Intel MPI's `omp` pin domain counts logical CPUs, and `compact` fills socket 0 first: 8 ranks × 8 threads on `omp`/`compact` gave 216 GB/s, while 64 ranks × 1 thread gave 435 GB/s.

The uncore, THP, EPB and C-state settings are machine-wide: each job sets them (sudo) and restores what it found on exit. Memory-side BIOS settings (SNC2, patrol scrub, memory power-down) can't be tuned: they need a reboot, and this node has no in-band BIOS tool. See [SYSTEM_TUNING.md](../hpl/SYSTEM_TUNING.md). STREAM's arrays are static, so only `THP=always` gives them huge pages (measured: 9.2 of 9.2 GB vs 0 under madvise).

```bash
scripts/Launchpad/stream/stream_smac3.sh                 # run until Ctrl-C (DURATION=300 to stop after 5 min)
python scripts/Launchpad/plot_tuning.py --run-group stream_smac3_v5 --metric triad_mb_s --scale 1e-3 --unit "GB/s"
```

- Config: `stream_smac3.yaml`. Run group `stream_smac3_v5`, seeded from `stream_smac3_v4` (which holds `stream_smac3_v3`, `stream_smac3_v2`, `stream_smac3` and, before them, the earlier random sweep in `outputs/stream/`).
- `runbest.sh`: one STREAM run with the best configuration (479.2 GB/s), written out in the script (build flags, threading, THP / uncore / C-states set and restored). `runbest_ngc.sh`: the same for NGC GPU STREAM (3446.5 GB/s on one H100). Logs in `outputs/runbest/`.
- `stream_autotune.py --config stream_smac3.yaml --check-replay` audits the history (no runs).
- HPL and STREAM tuners share a node lock, so their runs never overlap.
- Other files: `streamsweep.py`/`streamsweep.sh` (earlier random sweep -> `outputs/stream/`), `streambest.py` (rank its results), `stream.sh` (spack STREAM build), `stream_env.sh` (compiler / Intel MPI environment shared by the runners).
