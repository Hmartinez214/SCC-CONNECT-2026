# System and BIOS tuning for CPU-only HPL (Launchpad node)

**Node:** 2× Xeon Gold 6548Y+ (64 cores, HT on), 1 TB DDR5 (16 × 64 GB at 5200 MT/s), 2 NUMA nodes.

**Best so far:** 4138 GFLOPS (Intel HPL, single rank, NB ≈ 700). That's ~95% of the ~4.3 TFLOPS peak at the clock the power cap allows.

**What limits it:** each socket is held at **250 W** (its TDP), so all-core AVX-512 runs at ~2.0–2.5 GHz. Raising the limit from Linux (RAPL) is ignored; it's enforced below the OS. Most gains left on the CPU come from:
- getting more clock out of the same 250 W;
- removing interference;
- a larger N (the tuner now goes up to N ≈ 329k).

Expect a few percent at most from each item below. Change **one thing at a time** and A/B it with the tuner's best config at a fixed N (`hpl_smac3.yaml` with N fixed).

## 1. OS settings (runtime, reset on reboot)

Run as root (`sudo`). "Now" is the measured state.

| Setting | Now | Set for HPL | Revert |
|---|---|---|---|
| Automatic NUMA balancing | **0 (done)** | `sysctl -w kernel.numa_balancing=0` | `sysctl -w kernel.numa_balancing=1` |
| `numad` (userspace NUMA balancer, ~26% of a CPU) | running | `systemctl stop numad` | `systemctl start numad` |
| Uncore (mesh/LLC/memory controller) frequency | floats 0.8–2.5 GHz (~1.6 under HPL) | Try capping max at 2.0, then 1.6, then 1.2 GHz so more of the 250 W goes to the cores: `for d in /sys/devices/system/cpu/intel_uncore_frequency/package_0*_die_00; do echo 2000000 > $d/max_freq_khz; done` | `echo 2500000 > .../max_freq_khz` |
| CPU frequency governor | `performance`, min=max=4.1 GHz | (already right) | — |
| Kubernetes noise (cluster broken since 2026-09-29, crash-looping, ~0.75 CPU across all cores) | unconfined | Confine it to core 0 (CPUs 0,64): `for u in kubepods.slice kubelet.service containerd.service; do systemctl set-property --runtime $u AllowedCPUs=0,64; done` | `for u in kubepods.slice kubelet.service containerd.service; do systemctl revert $u; done` |
| Transparent huge pages | `madvise` | Leave it: Intel HPL already puts ~all its memory on THP | — |
| Reserved huge pages (only for `HPL_LARGEPAGE=1`) | 0 | See below | `sysctl -w vm.nr_hugepages=0` |
| Page cache before large-N runs | 600+ GB cached | `sync; echo 3 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory` | not needed |

**Reserving huge pages for `HPL_LARGEPAGE=1`**
- Pages needed ≈ N² × 8 B × 1.05 / 2 MiB. For N = 200k that's ~160k pages (~315 GB); for N = 329k, ~413k pages (~845 GB).
- Reserved pages are locked away from every other user until released. Reserve only for the run, then set it back to 0.
- Drop caches and compact memory first (last table row), then: `sysctl -w vm.nr_hugepages=<pages>`.
- Check `grep HugePages_ /proc/meminfo`: on a fragmented system the kernel may reserve fewer than you asked for.
- Then move `HPL_LARGEPAGE` from `fixed:` to `tunable:` in `hpl_smac3.yaml`, with a new `run_group` whose `priors` point at the current one. Without reserved pages every `HPL_LARGEPAGE=1` run fails with "Memory Allocation Error"; the tuner checks first and skips such runs in seconds.

**Before a run**
- No other heavy jobs on the node: check `top`, and your own STREAM/HPL tmux sessions.
- The tuner resets inherited CPU affinity itself. The default tmux server here was started restricted to socket 1, so anything launched by hand inside it is too. Check with `taskset -cp $$`.

## 2. BIOS settings (reboot; in rough order of expected value)

| Setting | Recommended | Why / how to check |
|---|---|---|
| Power / performance profile | **Maximum Performance** (not Balanced/Efficiency) | Stops firmware from trading frequency for efficiency under the 250 W cap |
| Hardware P-states (HWP) | **Native mode with HWP** (currently disabled: Linux runs `intel_pstate` passive with no HWP) | Faster, firmware-managed frequency; then (with `intel_pstate` in active mode) set the energy-performance preference: `echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference` |
| Energy Efficient Turbo | **Disabled** | It deliberately lowers turbo when it judges the gain is small |
| Turbo Boost | Enabled (already) | — |
| Package power limit (PL1/PL2) | Raise only if the BIOS exposes it | It's 250 W/socket and Linux writes are ignored. This SKU may not allow sustained power above TDP. If raised, watch temps (`sensors`, limit 89 °C) and PSU capacity |
| Uncore frequency | Leave dynamic, or set a fixed max | Same idea as the runtime uncore cap; the runtime sysfs test is quicker to A/B |
| Sub-NUMA clustering (SNC) | **Test SNC2** (currently off: 2 NUMA nodes) | 4 NUMA nodes, each closer to its memory. The v2 tuner reads the real NUMA layout, so GRID 1x2/2x1 become 2 nodes per rank and 2x2/1x4/4x1 one node per rank |
| Hyper-Threading | **Test off** | Intel HPL uses only physical cores (the 64 siblings sit idle). Off removes siblings that background tasks can land on; the tuner adapts (64 CPUs) |
| Memory frequency | Max the board/CPU allows | DIMMs are rated 5600 but run at 5200 MT/s. 5200 may be this SKU's limit; check the BIOS memory-speed setting. One DIMM reports no speed: check its seating/SPD |
| Hardware prefetchers (L2/DCU/LLC) | Enabled (default) | HPL benefits; only test disabling if the profile hints otherwise |
| C-states (C6, C1E) | Default, or disable for consistency | Little effect under full load; disabling can steady turbo between runs |
| Fan profile | **Maximum / full speed** | A cooler die leaks less, so more MHz within the same 250 W (currently ~60–68 °C under HPL) |
| Patrol scrub / memory power-down | Disable for benchmarking | Removes background memory activity |

## 3. Checking whether a change helped

```bash
sudo turbostat --quiet --interval 5 --show Package,Bzy_MHz,UncMHz,PkgWatt,RAMWatt,PkgTmp   # during a run
python scripts/Launchpad/hpl/hpl_autotune.py --config scripts/Launchpad/hpl/hpl_smac3.yaml --check-replay   # history sanity
python scripts/Launchpad/plot_compare.py smac3_intel_hpl smac3_intel_hpl_v2                                          # progress
```

**Rule of thumb:** GFLOPS ≈ 0.95 × 64 cores × 32 flops/cycle × average core clock (Bzy_MHz).
- If a change raises average Bzy_MHz at the same 250 W, it helps.
- If GFLOPS drops while Bzy_MHz holds steady, it's memory/NUMA placement (compare RAMWatt across the two sockets).
