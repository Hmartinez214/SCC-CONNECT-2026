# scripts/Launchpad/

| Path | Contents |
|---|---|
| `autotune_local.py` | Shared SMAC3 tuner runtime for this node (local scheduler, node lock, resumable SMAC, prior seeding, `--check-replay`). Used by `hpl/`, `stream/` and `mfc/`. |
| `plot_tuning.py`, `plot_compare.py`, `plot_params.py`, `plot_hpctools.py` | Plot one run group / compare several / GFLOPS vs every variable / HPC-Tools' own plotting (`outputs/autotuning/` -> `plots/hpl/`, `plots/stream/`). Shared palette and log reading: `plot_style.py`. |
| `export_best.py` | Write the best configurations and their performance for every benchmark to `outputs/best/` (tracked in git). |
| `tests/` | `venv/bin/python -m pytest scripts/Launchpad/tests -q`: job settings restored on exit/error/SIGTERM, Intel HPL rank placement, result parsing, prior import, replay round trip (no benchmarks run). |
| `hpl/` | HPL autotuning (Intel and netlib HPL), system/BIOS tuning guide. |
| `stream/` | STREAM autotuning and the earlier random sweep. |
| `deps/` | Build OpenBLAS / Open MPI with HPC-Tools; install Intel oneAPI. |
| `mfc/` | MFC on the H100s: build, timed runs, build-variant matrix, answer checks, SMAC3 tuning. |
| `wrf/` | Build WRF/WPS. |
