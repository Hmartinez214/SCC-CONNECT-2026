# experiments/

Node experiments behind the findings in `../hpl/README.md` and `../stream/README.md`. Each takes the node lock, restores what it changes, and writes to `outputs/experiments/<name>/`. Run them through `bench exp NAME ...`; `-h` on any script shows its options.

| Script | What it measures | Result on this node (2026-10-29) |
|---|---|---|
| `ab.sh [-r ROUNDS] BENCH 'ARGS A' 'ARGS B' ...` | Interleaved A/B(/C...) of any `bench run` settings, rotated order per round; mean, sd and difference from A. | Core 0 excluded: -0.7% (4238 vs 4269 GFLOPS); 2 MB hugetlbfs vs THP: tie; 1 GB pages at N=50000: +0.7%. |
| `pl1.sh [N=90482] WATTS...` | Intel HPL at each package power limit (MSR 0x610), with clocks, power and temperature from turbostat; watchdog at `LIMIT_C`. | 250/275/300 W all ~4255 GFLOPS at ~249 W: the limit is the voltage regulator current (VR_TDC), not PL1. |
| `fwq.sh [SECS=30]` + `fwq.c` | OS noise: time taken from a spinning thread on every CPU. | 0.53% mean with Kubernetes stopped (0.71% with it); core 0 (CPUs 0/64) noisiest. |
| `bw.sh [THREADS=64] [base ht imc pf=0x..]` + `bw.c` | Memory-bandwidth ceilings: read-only, streaming write, copy, triad; optional IMC counters and prefetcher MSR 0x1a4 settings. | Read 543, write ~400 GB/s of 665.6 nameplate; STREAM Triad is 98-99% of the 2R:1W mix ceiling; no prefetcher setting helped. |
