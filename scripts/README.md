# scripts/

Per-machine job and build scripts.

| Path | Contents |
|---|---|
| `Launchpad/` | The Launchpad node (2x Xeon 6548Y+, 2x H100 NVL, no Slurm): HPL/STREAM autotuning, dependency builds, MFC and WRF builds. |
| `Perlmutter/` | NERSC Perlmutter Slurm scripts (MFC parameter sweep, WRF run). |
| `Hummingbird/` | UCSC Hummingbird cluster Slurm scripts (MFC job, WRF install attempt). |
| `analysis/` | Post-processing (MFC output parsing). |
| `job_template.bat` | Slurm batch-script template. |
