# NCCL findings — 2×A100 (Perlmutter GPU node)

Results and notes from tuning NCCL collectives. Tooling that produced these is
in this folder (`scripts/`, `tuning/`); see `README.md` to reproduce.

## Setup

- 2× NVIDIA A100-SXM4-40GB on one Perlmutter GPU node (`-q shared_interactive -C gpu --gpus=2`)
- GPU0↔GPU1: **`NV4`** = 4 bonded NVLink3 links = ~100 GB/s per direction, ~200 GB/s bidirectional
- NCCL 2.29.2 (NERSC module), CUDA 13.2, `sm_80`
- Transport confirmed healthy: `via P2P/direct pointer/read` on all 8 channels, NVLink, 0 validation errors

## Baseline (NCCL defaults) — large-message peak busbw

| collective | peak busbw | notes |
|---|---:|---|
| broadcast | **88.8 GB/s** | one-way only → ~89% of the 100 GB/s NV4 limit; this is the practical ceiling |
| sendrecv | 74.6 | both directions at once |
| all_reduce | 74.4 | |
| reduce_scatter | 65.2 | weakest — and it's an FSDP collective |
| all_gather | 65.4 | weakest — FSDP collective |

Latency floor (8 B): ~11 µs. Bandwidth regime starts ~256 KB–1 MB, near-peak by ~64–128 MB.

nccl-tests scales each collective so a perfectly tuned system reports the *same*
busbw for all_reduce / reduce_scatter / all_gather / sendrecv. reduce_scatter &
all_gather sitting ~10 below all_reduce and ~24 below broadcast = tuning headroom.

## Sweep — all_reduce, 1M–8G, `NCCL_ALGO` × `NCCL_PROTO` × channels

| result | number | takeaway |
|---|---|---|
| best | Tree/Simple/16ch = **79.6** (Ring/Simple/16ch = 79.0) | +7% over the 74.4 default |
| channels=16 | ~79 vs ~74 default | forcing more channels than NCCL's default (8) helps a bit |
| channels=4 | ~53 | **do not force channels below NCCL's default** — starves the link |
| Simple vs LL128 vs LL | 79 / 74 / 38 (at 16ch) | Simple wins the bandwidth regime; LL/LL128 are for small messages only |
| Ring vs Tree | 79.6 vs 79.0 | within noise for 2 GPUs (trivial rings/trees) |

Sweep used `-n 30 -w 10`, so ±2 GB/s is noise. The ch=4 cliff and the ch=16
gain are real; Ring-vs-Tree is not.

**Candidate config for all_reduce on this hardware:**
```
export NCCL_MIN_NCHANNELS=16
export NCCL_MAX_NCHANNELS=16
# NCCL_PROTO=Simple and NCCL_ALGO are already the effective defaults
```
Caveat: 16 channels = 16 SMs spent on comms that a real workload wants for
compute — verify the win end-to-end in an actual run, not just the microbench.

## Next

- Confirm the ch16 gain with a full run (`-n 50`), and sweep `reduce_scatter` / `all_gather` (bigger headroom).
- `tuning/` runs SMAC3 over `{algo, proto, channels, buffsize, nthreads}`; submit `tuning/tune.sbatch` as a batch job. View with DeepCAVE (`tuning/README.md`).
- If different message sizes want different configs, build an `NCCL_TUNER_CONFIG_FILE` (size-range → algo/proto/channels).

## Collective reference

N GPUs, each holds an array. `[a0]` = GPU 0's data.

| op | before → after | one line | used by |
|---|---|---|---|
| Broadcast | root `[A]` → all `[A]` | one GPU's data to everyone | initial weight distribution |
| Reduce | `[a0] [a1]` → root `[a0+a1]` | sum onto one GPU | logging a global loss/metric |
| **AllReduce** | `[a0] [a1]` → all `[a0+a1]` | sum, result everywhere | **DDP gradient averaging, every step** |
| **ReduceScatter** | `[a0,b0] [a1,b1]` → `[a0+a1]`, `[b0+b1]` | sum, each GPU keeps one slice | FSDP/ZeRO gradient shard |
| **AllGather** | `[a]`, `[b]` → all `[a,b]` | slices gathered to everyone | FSDP weight un-shard before a layer |
| AllToAll | each GPU sends a different chunk to each | distributed transpose | Mixture-of-Experts routing |
| SendRecv | GPU i → GPU j | point-to-point building block | pipeline-parallel activations |

`AllReduce = ReduceScatter + AllGather` — literally how NCCL's ring all_reduce
works internally. DDP calls `all_reduce`; FSDP calls the two halves separately
so it can overlap each with compute.
