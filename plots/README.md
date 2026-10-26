# plots/

Generated figures, one folder per benchmark (images are gitignored; regenerate with the scripts below).

| Folder | Contents | Made by |
|---|---|---|
| `hpl/` | `<run_group>_progress.png`: one run group's GFLOPS per run + best so far | `scripts/Launchpad/plot_tuning.py --run-group <group>` |
| | `hpl_compare.png`: run groups overlaid | `scripts/Launchpad/plot_compare.py` |
| `hpl/params/` | `<VARIABLE>.png`: GFLOPS vs one tuned variable (24 files: N, NB, GRID, UNCORE_MAX_MHZ, HPL_* knobs, HPL.dat settings); `all_variables.png`: all of them on one page | `scripts/Launchpad/plot_params.py` |
| | `hpctools_hpl.png`: HPC-Tools' own plotting (per run + running best) | `scripts/Launchpad/plot_hpctools.py` |
| `stream/` | `stream_smac3_progress.png`, `hpctools_stream.png`: STREAM Triad | `plot_tuning.py --run-group stream_smac3 ...`, `plot_hpctools.py` |
| `wrf/` | `santacruz_d02.gif`, `satellite_d01.gif`: WRF simulation renders | (restored from commit bb52988) |

STREAM run groups (names starting with `stream`) go to `stream/`, everything else to `hpl/`.
