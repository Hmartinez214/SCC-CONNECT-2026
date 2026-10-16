# STREAM autotuning (SMAC3)

Compiles `stream.c` with spack's icx and tunes compile flags, array size, NTIMES, OFFSET, OpenMP threads/affinity and numactl policy. Objective: Triad MB/s. A run only counts if STREAM validates. Arrays are always ≥ 1 GiB, so results measure memory, not cache.

```bash
DURATION=300 scripts/Launchpad/stream/stream_smac3.sh   # run 5 min (omit DURATION to run until Ctrl-C)
python scripts/Launchpad/plot_tuning.py --run-group stream_smac3 --metric triad_mb_s --scale 1e-3 --unit "GB/s"
```

- Config: `stream_smac3.yaml`. Results: `outputs/autotuning/stream_smac3/evaluations.jsonl`.
- The first start seeds SMAC with the compatible trials from the earlier random sweep (`outputs/stream/trials.jsonl`, via `streamsweep.py`).
- `stream_autotune.py --config stream_smac3.yaml --check-replay` audits that history (no runs).
- HPL and STREAM tuners share a node lock, so their runs never overlap.
