# scripts/Launchpad/

| Path | Contents |
|---|---|
| `autotune_local.py` | Shared SMAC3 tuner runtime for this node (local scheduler, node lock, resumable SMAC, prior seeding, `--check-replay`). Used by `hpl/` and `stream/`. |
| `plot_tuning.py`, `plot_compare.py` | Plot one run group / compare several (`outputs/autotuning/` -> `plots/`). |
| `hpl/` | HPL autotuning (Intel and netlib HPL), system/BIOS tuning guide. |
| `stream/` | STREAM autotuning and the earlier random sweep. |
| `deps/` | Build OpenBLAS / Open MPI with HPC-Tools; install Intel oneAPI. |
| `mfc/` | Build MFC with GPU (OpenACC) support. |
| `wrf/` | Build WRF/WPS. |
