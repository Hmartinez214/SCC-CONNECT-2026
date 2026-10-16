# HPL autotuning (SMAC3)

Tunes HPL with HPC-Tools' SMAC3 adapter. The objective is GFLOPS, and a run only counts if the residual check passes.

- **`hpl_smac3.yaml` (default): Intel's Distribution for LINPACK** (`xhpl_intel64_dynamic` from spack MKL's `mp_linpack`). Space: HPL.dat params, N up to ~329k (80% of RAM; runs up to ~1.5 h), NB 256–2048, `GRID` up to 8 ranks (`HPL_HOST_NODE` / `HPL_HOST_CORE`), `MEM_POLICY`. Fixed: `HPL_LOG=2`, `HPL_LARGEPAGE=0` (make it tunable only after reserving huge pages, see SYSTEM_TUNING.md). Run group `smac3_intel_hpl_v2`, seeded from the first Intel run group `smac3_intel_hpl` (small N, 3 grids).
- **`hpl_netlib_smac3.yaml`: spack's netlib HPL** (`hpl/zqzkvyu`: gcc + MKL + Intel MPI). Space: HPL.dat params, N, `RANKS` (64/128), `MPI_BIND`, `MPI_MAP`, `MEM_POLICY`, `HUGEPAGES`. Run group: `smac3_launchpad_ranks`.

```bash
scripts/Launchpad/hpl/hpl_smac3_tmux.sh                                        # Intel HPL, tmux session hpl-smac3
scripts/Launchpad/hpl/hpl_smac3_tmux.sh scripts/Launchpad/hpl/hpl_netlib_smac3.yaml   # netlib HPL
tmux kill-session -t hpl-smac3                                                 # stop (resumable)
python scripts/Launchpad/plot_tuning.py --run-group smac3_intel_hpl            # plots/<run_group>_progress.png
```

- Restarts replay the log into SMAC. `hpl_autotune.py --config <yaml> --check-replay` audits that replay (no runs).
- Changing the search space needs a new `run_group`.
- Each socket is held at 250 W under HPL (~2.0–2.5 GHz). Raising the power limit from Linux has no effect. OS/BIOS tuning: [SYSTEM_TUNING.md](SYSTEM_TUNING.md).
- Other files: `hpl.sh` installs spack's oneAPI-compiled HPL (`hpl/gqvmmph`). That build fails HPL's residual check, so the tuners use `hpl/zqzkvyu` (netlib) and Intel's MKL binary instead. `hpl_smac3_tmux.sh` starts `hpl_smac3.sh` in a tmux session.
