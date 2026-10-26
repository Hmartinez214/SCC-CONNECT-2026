# Best configurations

Written by `scripts/Launchpad/export_best.py` from the raw logs in `outputs/` (which are not in git).

| Benchmark | Best | Where | File |
|---|---|---|---|
| Intel HPL, CPU, large N | 4,403.6 GFLOPS at N=324,520 | hpl_bign | `hpl_intel.json` (`large_n`) |
| Intel HPL, CPU, tuner (N ~90k) | 4,287.9 GFLOPS | smac3_intel_hpl_fast attempt 355 | `hpl_intel.json` (`top`) |
| netlib HPL, CPU | 3,171.3 GFLOPS | smac3_launchpad_ranks attempt 110 | `hpl_netlib.json` |
| STREAM, CPU (Triad) | 479.2 GB/s | stream_smac3_v5 attempt 3716 | `stream.json` |
| NGC STREAM, 1 GPU (Triad) | 3,446.5 GB/s | ngc_stream_smac3 attempt 4 | `ngc_stream.json` |
| NGC HPL, 2 GPUs | 69.15 TFLOPS at N=136,192 | `outputs/ngc_hpl/20261026_065743.log` | `ngc_hpl.json` |
| MFC, 2 GPUs | best config for each of 158 cases | 164 run groups | `mfc.json` |
| NCCL all_reduce | 247.63 GB/s (default 233.89) | `outputs/nccl_launchpad/tune_20261026.log` | `nccl.json` |
| NCCL all_gather | 238.83 GB/s (default 194.79) | `outputs/nccl_launchpad/tune_20261026.log` | `nccl.json` |
| NCCL reduce_scatter | 240.05 GB/s (default 199.71) | `outputs/nccl_launchpad/tune_20261026.log` | `nccl.json` |
| NCCL broadcast | 251.58 GB/s (default 246.35) | `outputs/nccl_launchpad/tune_20261026.log` | `nccl.json` |
