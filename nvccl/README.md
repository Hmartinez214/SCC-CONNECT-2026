# nvccl - NCCL build / benchmark / inspect

Portable helpers for NVIDIA nccl-tests. Auto-detects CUDA, NCCL, gencode and
GPU count.

    source scripts/pm.sh      # Perlmutter: modules + proxy + env.sh
    # (elsewhere: just `source env.sh`)
    scripts/setup.sh          # fetch nccl-tests (pinned commit)
    scripts/build.sh          # build for the local GPU arch
    scripts/bench.sh all_reduce 8 8G       # NGPUS ranks, size sweep + parse
    scripts/sweep.sh all_reduce 1M 8G      # grid over NCCL_ALGO x PROTO x channels
    scripts/explore.sh                     # dump NCCL's topology/graph decisions

`busbw` (bus bandwidth) is the headline number: link utilisation, comparable to
the interconnect peak. Small messages are latency-bound (watch `time`); large
messages should plateau near peak.

Tuning knobs (set as env vars, re-run bench): `NCCL_ALGO` (Ring/Tree/NVLS),
`NCCL_PROTO` (LL/LL128/Simple), `NCCL_MIN_NCHANNELS`/`NCCL_MAX_NCHANNELS`,
`NCCL_BUFFSIZE`, `NCCL_NTHREADS`. See `scripts/sweep.sh`.

Notifications: `build.sh`/`bench.sh`/`sweep.sh` post via ntfy if `NTFY_TOPIC`
and `NTFY_TOKEN` are exported; silent no-op otherwise.
