#!/usr/bin/env python3
"""Randomized STREAM compile-and-run explorer.

Example:
  python3 stream_fuzz.py --source ~/src/stream/stream.c --compiler icx

Runs continuously until Ctrl-C by default. Each attempted build/run is recorded
under --output. Failed compilations and invalid benchmark runs are retained.

Memory guard: STREAM's three arrays use approximately
  3 * STREAM_ARRAY_SIZE * sizeof(STREAM_TYPE)
bytes, plus small overhead. The default cap is 12 GiB.

The script randomly explores compiler flags, STREAM compile-time settings,
OpenMP placement, and (when numactl is installed) NUMA memory/CPU policies.
System-wide transparent huge-page policy is intentionally not changed by this
script; see the accompanying notes for how to test it safely.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

STOP = False


def stop_handler(_signum, _frame):
    global STOP
    STOP = True


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_args():
    p = argparse.ArgumentParser(description="Continuously randomize STREAM build and runtime settings.")
    p.add_argument("--source", required=True, help="Path to STREAM source file, usually stream.c")
    p.add_argument("--compiler", default="icx", help="C compiler executable (default: icx)")
    p.add_argument("--output", default="stream_random_sweeps", help="Output directory")
    p.add_argument("--trials", type=int, default=0, help="Number of new trials; 0 means continuous until Ctrl-C")
    p.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    p.add_argument("--max-array-gib", type=float, default=12.0,
                   help="Maximum memory for the three STREAM arrays in GiB (default: 12)")
    p.add_argument("--max-threads", type=int, default=128, help="Maximum OMP thread count to test")
    p.add_argument("--timeout", type=float, default=180.0, help="Per-run timeout in seconds")
    p.add_argument("--compile-timeout", type=float, default=120.0, help="Per-build timeout in seconds")
    p.add_argument("--extra-cflag", action="append", default=[],
                   help="Extra fixed compiler flag; repeatable, e.g. --extra-cflag=-g")
    p.add_argument("--no-numa", action="store_true",
                   help="Disable numactl CPU/memory-policy experiments even if numactl is installed")
    p.add_argument("--keep-binaries", action="store_true", help="Keep successful trial binaries")
    return p.parse_args()


def run_command(cmd, *, env=None, timeout=120):
    start = time.monotonic()
    try:
        cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", env=env, timeout=timeout)
        return cp.returncode, cp.stdout, time.monotonic() - start, False
    except subprocess.TimeoutExpired as e:
        output = e.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return 124, output + "\n[TIMEOUT]\n", time.monotonic() - start, True
    except OSError as e:
        return 127, str(e), time.monotonic() - start, False


def detect_compiler_family(compiler_path):
    """Best-effort family detection from the executable name."""
    name = Path(compiler_path[0]).name.lower()
    if name in {"icx", "icpx", "icc", "icpc"} or name.startswith(("icx-", "icc-")):
        return "intel"
    if name in {"nvc", "nvc++", "nvcc"} or name.startswith("nvc-"):
        return "nvhpc"
    if name in {"clang", "clang++", "cc", "gcc", "g++"} or name.startswith(("gcc-", "clang-")):
        # 'cc' is ambiguous; assume GCC/Clang-compatible options.
        return "gcc_clang"
    return "unknown"


def compiler_option_sets(family, rng):
    """Return compatible-ish option candidates for the detected compiler family.

    Unsupported combinations are intentionally retained as failed trials, but
    options are grouped by compiler family to avoid systematic cross-family noise.
    """
    opt = rng.choice(["-O2", "-O3", "-Ofast"])

    if family == "intel":
        isa_name, isa_flags = rng.choice([
            ("compiler_default", []),
            ("host", ["-xHost"]),
            ("avx2", ["-xCORE-AVX2"]),
            ("avx512", ["-xCORE-AVX512", "-qopt-zmm-usage=high"]),
            ("march_native", ["-march=native"]),
            ("march_mtune_native", ["-march=native", "-mtune=native"]),
        ])
        store_name, store_flags = rng.choice([
            ("streaming_auto", ["-qopt-streaming-stores=auto"]),
            ("streaming_always", ["-qopt-streaming-stores=always"]),
            ("streaming_never", ["-qopt-streaming-stores=never"]),
        ])
        extras = [
            ("no_extra", []),
            ("unroll_loops", ["-funroll-loops"]),
            ("fast_math", ["-ffast-math"]),
            ("lto", ["-flto"]),
            ("alias_analysis", ["-fno-alias"]),
        ]
        report = rng.choice([
            ("report_off", []),
            ("vector_report", ["-qopt-report=5", "-qopt-report-phase=vec"]),
        ])
        model_name, model_flags = rng.choice([
            ("default_model", []),
            ("medium_model", ["-mcmodel=medium"]),
        ])
    elif family == "nvhpc":
        isa_name, isa_flags = ("compiler_default", [])
        store_name, store_flags = ("compiler_default", [])
        extras = [
            ("no_extra", []),
            ("fast", ["-fast"]),
            ("simd_vectorization", ["-Mvect=simd"]),
            ("cache_align", ["-Mcache_align"]),
            ("fast_plus_simd", ["-fast", "-Mvect=simd"]),
        ]
        report = rng.choice([
            ("report_off", []),
            ("vector_report", ["-Minfo=all"]),
        ])
        model_name, model_flags = rng.choice([
            ("default_model", []),
            ("medium_model", ["-mcmodel=medium"]),
        ])
    elif family == "gcc_clang":
        isa_name, isa_flags = rng.choice([
            ("compiler_default", []),
            ("march_native", ["-march=native"]),
            ("march_mtune_native", ["-march=native", "-mtune=native"]),
        ])
        store_name, store_flags = ("compiler_default", [])
        extras = [
            ("no_extra", []),
            ("unroll_loops", ["-funroll-loops"]),
            ("fast_math", ["-ffast-math"]),
            ("lto", ["-flto"]),
            ("strict_aliasing", ["-fstrict-aliasing"]),
        ]
        report = rng.choice([
            ("report_off", []),
            ("vector_report", ["-fopt-info-vec-optimized", "-fopt-info-vec-missed"]),
        ])
        model_name, model_flags = rng.choice([
            ("default_model", []),
            ("medium_model", ["-mcmodel=medium"]),
        ])
    else:
        # Unknown compilers get conservative options; extra flags can be supplied
        # by the user via --extra-cflag.
        isa_name, isa_flags = ("compiler_default", [])
        store_name, store_flags = ("compiler_default", [])
        extras = [("no_extra", [])]
        report = ("report_off", [])
        model_name, model_flags = ("default_model", [])

    extra_name, extra_flags = rng.choice(extras)
    return {
        "optimization": opt,
        "isa": isa_name,
        "isa_flags": isa_flags,
        "streaming_stores": store_name,
        "store_flags": store_flags,
        "extra_optimization": extra_name,
        "extra_flags": extra_flags,
        "vector_report": report[0],
        "report_flags": report[1],
        "memory_model": model_name,
        "model_flags": model_flags,
    }


def choose_numa_mode(rng, args, numactl_path):
    if args.no_numa or not numactl_path:
        return "default", []
    # CPU-node binding deliberately pairs CPU and memory to one NUMA node;
    # memory-only policies help isolate placement effects while OpenMP controls CPUs.
    return rng.choice([
        ("default", []),
        ("interleave_all", [numactl_path, "--interleave=all"]),
        ("membind_0", [numactl_path, "--membind=0"]),
        ("membind_1", [numactl_path, "--membind=1"]),
        ("cpu_mem_node_0", [numactl_path, "--cpunodebind=0", "--membind=0"]),
        ("cpu_mem_node_1", [numactl_path, "--cpunodebind=1", "--membind=1"]),
    ])


def choose_config(rng, args, family, numactl_path):
    # Include cache-resident and memory-resident workloads, subject to the cap
    # for all three arrays. STREAM_ARRAY_SIZE is an element count, not bytes.
    type_name, bytes_per = rng.choice([("double", 8), ("float", 4)])
    max_elements = int(args.max_array_gib * (1024**3) / (3 * bytes_per))
    candidates = [
        100_000, 250_000, 500_000,
        1_000_000, 4_000_000, 8_000_000, 16_000_000, 32_000_000,
        67_108_864,       # 64 Mi elements
        134_217_728,      # 128 Mi
        268_435_456,      # 256 Mi
        402_653_184,      # 384 Mi
        536_870_912,      # 512 Mi
        805_306_368,      # 768 Mi
        1_073_741_824,    # 1 Gi elements
        2_147_483_648,    # 2 Gi elements
        4_294_967_296,    # 4 Gi elements
        8_589_934_592,    # 8 Gi elements
        17_179_869_184,   # 16 Gi elements
        34_359_738_368,   # 32 Gi elements
        68_719_476_736,   # 64 Gi elements
        137_438_953_472,  # 128 Gi elements
        274_877_906_944,  # 256 Gi elements
        549_755_813_888,  # 512 Gi elements
        1_099_511_627_776 # 1 Ti elements
    ]
    sizes = [n for n in candidates if n <= max_elements]
    if not sizes:
        sizes = [max(100_000, max_elements)]
    array_size = rng.choice(sizes)
    ntimes = rng.choice([10, 20, 30, 50, 100])
    offset = rng.choice([0, 1, 8, 16, 32, 64])

    compiler_cfg = compiler_option_sets(family, rng)
    openmp_flag = "-qopenmp" if family == "intel" else ("-mp" if family == "nvhpc" else "-fopenmp")
    compile_flags = [compiler_cfg["optimization"], openmp_flag]
    compile_flags += compiler_cfg["isa_flags"]
    compile_flags += compiler_cfg["store_flags"]
    compile_flags += compiler_cfg["model_flags"]
    compile_flags += compiler_cfg["extra_flags"]
    compile_flags += compiler_cfg["report_flags"]

    affinity = rng.choice([
        "granularity=fine,compact,1,0",
        "granularity=fine,scatter,1,0",
        "granularity=core,compact,1,0",
        "granularity=fine,balanced",
        "disabled",
    ])
    proc_bind = rng.choice(["close", "spread", "true", "false"])
    places = rng.choice(["cores", "threads", "sockets"])
    threads = [n for n in [1, 2, 4, 8, 16, 32, 64, 96, 128] if n <= args.max_threads]
    if not threads:
        threads = [1]
    omp_threads = rng.choice(threads)
    numa_mode, numa_prefix = choose_numa_mode(rng, args, numactl_path)

    defs = [
        f"-DSTREAM_ARRAY_SIZE={array_size}",
        f"-DNTIMES={ntimes}",
        f"-DOFFSET={offset}",
        f"-DSTREAM_TYPE={type_name}",
    ]
    return {
        "array_size": array_size,
        "ntimes": ntimes,
        "offset": offset,
        "stream_type": type_name,
        "element_bytes": bytes_per,
        "array_memory_gib": 3 * array_size * bytes_per / (1024**3),
        "optimization": compiler_cfg["optimization"],
        "isa": compiler_cfg["isa"],
        "streaming_stores": compiler_cfg["streaming_stores"],
        "extra_optimization": compiler_cfg["extra_optimization"],
        "vector_report": compiler_cfg["vector_report"],
        "memory_model": compiler_cfg["memory_model"],
        "compile_flags": compile_flags,
        "definitions": defs,
        "omp_threads": omp_threads,
        "kmp_affinity": affinity,
        "omp_proc_bind": proc_bind,
        "omp_places": places,
        "omp_dynamic": "FALSE",
        "numa_mode": numa_mode,
        "numa_prefix": numa_prefix,
    }


def parse_stream_output(output):
    result = {"validates": "Solution Validates:" in output}
    for kernel in ("Copy", "Scale", "Add", "Triad"):
        # Standard STREAM output: Kernel: bandwidth avg_time min_time max_time
        pattern = rf"(?m)^\s*{kernel}:\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)"
        match = re.search(pattern, output)
        if match:
            result[kernel.lower() + "_mb_s"] = float(match.group(1))
            result[kernel.lower() + "_avg_s"] = float(match.group(2))
            result[kernel.lower() + "_min_s"] = float(match.group(3))
            result[kernel.lower() + "_max_s"] = float(match.group(4))
    return result


def next_trial_number(jsonl_path):
    """Continue numbering when resuming into an existing output directory."""
    last = 0
    if not jsonl_path.exists():
        return 1
    with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                last = max(last, int(json.loads(line).get("trial", 0)))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
    return last + 1


def main():
    args = parse_args()
    source = Path(args.source).expanduser().resolve()
    if not source.is_file():
        sys.exit(f"ERROR: source file does not exist: {source}")
    if args.max_array_gib <= 0:
        sys.exit("--max-array-gib must be positive")
    if args.max_threads < 1:
        sys.exit("--max-threads must be at least 1")
    if args.trials < 0:
        sys.exit("--trials cannot be negative")

    out = Path(args.output).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    logs = out / "logs"
    logs.mkdir(exist_ok=True)
    binaries = out / "binaries"
    if args.keep_binaries:
        binaries.mkdir(exist_ok=True)

    rng = random.Random(args.seed)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    compiler_path = shlex.split(args.compiler)
    if not compiler_path:
        sys.exit("Compiler command is empty.")
    family = detect_compiler_family(compiler_path)
    numactl_path = shutil.which("numactl")

    code, version, _, _ = run_command(compiler_path + ["--version"], timeout=15)
    if code != 0:
        print(version, file=sys.stderr)
        sys.exit(f"ERROR: compiler check failed (exit {code}): {args.compiler}")
    (out / "compiler_version.txt").write_text(version, encoding="utf-8")
    (out / "compiler_family.txt").write_text(family + "\n", encoding="utf-8")

    print(f"Source: {source}")
    print(f"Compiler: {args.compiler} (detected family: {family})")
    print(f"Output: {out}")
    print(f"Seed: {args.seed!r}")
    print(f"numactl: {numactl_path or 'not found'}" + (" (disabled)" if args.no_numa else ""))
    print("OpenMP dynamic teams are disabled for repeatability.")
    print("Press Ctrl-C to stop after the current subprocess finishes.")

    jsonl_path = out / "trials.jsonl"
    csv_path = out / "trials.csv"
    csv_fields = [
        "trial", "timestamp_utc", "status", "return_code", "build_seconds", "run_seconds",
        "array_size", "ntimes", "offset", "stream_type", "array_memory_gib",
        "omp_threads", "kmp_affinity", "omp_proc_bind", "omp_places", "omp_dynamic",
        "numa_mode", "optimization", "isa", "streaming_stores", "extra_optimization",
        "vector_report", "memory_model", "copy_mb_s", "scale_mb_s", "add_mb_s", "triad_mb_s",
        "validates", "compile_command", "run_command", "error_or_note",
    ]
    existing_csv = csv_path.exists() and csv_path.stat().st_size > 0
    csv_file = csv_path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_file, fieldnames=csv_fields, extrasaction="ignore")
    if not existing_csv:
        writer.writeheader()
        csv_file.flush()

    trial = next_trial_number(jsonl_path) - 1
    start_trial = trial + 1
    completed_this_invocation = 0
    try:
        while not STOP and (args.trials == 0 or completed_this_invocation < args.trials):
            trial += 1
            completed_this_invocation += 1
            cfg = choose_config(rng, args, family, numactl_path)
            trial_tag = f"trial_{trial:06d}_{int(time.time())}"
            binary = (binaries if args.keep_binaries else out) / (trial_tag + ".exe")
            command = compiler_path + cfg["definitions"] + cfg["compile_flags"] + args.extra_cflag + [
                str(source), "-o", str(binary)
            ]
            print(
                f"[{trial}] build {cfg['stream_type']} N={cfg['array_size']:,} "
                f"NTIMES={cfg['ntimes']} threads={cfg['omp_threads']} "
                f"NUMA={cfg['numa_mode']} ISA={cfg['isa']} opt={cfg['optimization']} "
                f"extra={cfg['extra_optimization']} stores={cfg['streaming_stores']} "
                f"flags={shlex.join(cfg['compile_flags'])}",
                flush=True,
            )
            build_rc, build_output, build_s, build_timeout = run_command(
                command, timeout=args.compile_timeout
            )
            (logs / f"{trial_tag}.build.log").write_text(build_output, encoding="utf-8")
            run_rc, run_output, run_s, run_timeout = None, "", 0.0, False
            metrics = {}
            status = "compile_failed"
            note = ""
            run_command_line = []
            if build_rc == 0:
                env = os.environ.copy()
                env.update({
                    "OMP_NUM_THREADS": str(cfg["omp_threads"]),
                    "KMP_AFFINITY": cfg["kmp_affinity"],
                    "OMP_PROC_BIND": cfg["omp_proc_bind"],
                    "OMP_PLACES": cfg["omp_places"],
                    "OMP_DYNAMIC": cfg["omp_dynamic"],
                })
                run_command_line = cfg["numa_prefix"] + [str(binary)]
                run_rc, run_output, run_s, run_timeout = run_command(
                    run_command_line, env=env, timeout=args.timeout
                )
                (logs / f"{trial_tag}.run.log").write_text(run_output, encoding="utf-8")
                metrics = parse_stream_output(run_output)
                if run_timeout:
                    status, note = "run_timeout", "Benchmark timed out"
                elif run_rc != 0:
                    status, note = "run_failed", f"exit code {run_rc}"
                elif not metrics.get("validates", False):
                    status, note = "invalid_or_unrecognized_output", "No validation line found"
                elif not all(k in metrics for k in ("copy_mb_s", "scale_mb_s", "add_mb_s", "triad_mb_s")):
                    status, note = "invalid_or_unrecognized_output", "Could not parse all STREAM kernel rows"
                else:
                    status = "ok"
            else:
                note = "compile timeout" if build_timeout else f"compiler exit code {build_rc}"

            record = {
                "trial": trial,
                "timestamp_utc": utc_now(),
                "status": status,
                "return_code": run_rc if run_rc is not None else build_rc,
                "build_seconds": round(build_s, 4),
                "run_seconds": round(run_s, 4),
                **cfg,
                "compile_command": shlex.join(command),
                "run_command": shlex.join(run_command_line) if run_command_line else "",
                "metrics": metrics,
                "error_or_note": note,
                "build_log": str(logs / f"{trial_tag}.build.log"),
                "run_log": str(logs / f"{trial_tag}.run.log") if build_rc == 0 else None,
                "source": str(source),
                "source_sha256": source_hash,
                "compiler": args.compiler,
                "compiler_family": family,
            }
            with jsonl_path.open("a", encoding="utf-8") as jf:
                jf.write(json.dumps(record) + "\n")

            row = {k: record.get(k, "") for k in csv_fields}
            row.update({
                "compile_command": shlex.join(command),
                "run_command": shlex.join(run_command_line) if run_command_line else "",
                "copy_mb_s": metrics.get("copy_mb_s", ""),
                "scale_mb_s": metrics.get("scale_mb_s", ""),
                "add_mb_s": metrics.get("add_mb_s", ""),
                "triad_mb_s": metrics.get("triad_mb_s", ""),
                "validates": metrics.get("validates", ""),
            })
            writer.writerow(row)
            csv_file.flush()

            if status == "ok":
                print(
                    f"    OK: Triad={metrics.get('triad_mb_s', float('nan')):.1f} MB/s; "
                    f"build={build_s:.1f}s run={run_s:.1f}s",
                    flush=True,
                )
            else:
                print(f"    {status}: {note}; see {logs / (trial_tag + '.build.log')}", flush=True)

            if not args.keep_binaries:
                try:
                    binary.unlink(missing_ok=True)
                except OSError:
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        csv_file.close()
        print(f"\nStopped after {completed_this_invocation} new trials (IDs {start_trial}..{trial}). Results: {csv_path}")
        print(f"Detailed records: {jsonl_path}")
        print(f"Logs: {logs}")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    main()
