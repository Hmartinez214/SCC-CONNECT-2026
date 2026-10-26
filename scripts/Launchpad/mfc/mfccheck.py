#!/usr/bin/env python3
"""Check and compare MFC runs made with runmfc.sh.

    # Is a faster variant still giving the right answer? Run both with --keep
    # (same case, ranks, --gbpp and --steps), then compare their D/ output:
    python3 scripts/Launchpad/mfc/mfccheck.py compare outputs/mfc_runs/ref outputs/mfc_runs/fast
    python3 scripts/Launchpad/mfc/mfccheck.py compare REF RUN --reltol 1e-4 --abstol 1e-10

    # Table of timed runs (grind time, speedup over the double/no-case-opt run):
    python3 scripts/Launchpad/mfc/mfccheck.py summary                       # all of outputs/mfc_runs
    python3 scripts/Launchpad/mfc/mfccheck.py summary outputs/mfc_runs/variants_20261017-120000

``compare`` reads the text fields simulation writes to D/ (benchmark cases
set parallel_io = F and prim_vars_wrt = T) with MFC's own test-suite packer,
judges them with MFC's tolerance rules (the run given first is the
reference), and prints the worst absolute/relative difference per file so a
borderline result can be judged by eye. Exit status 0 = within tolerance.

Use a small problem for this: D/ is plain text (even --gbpp 1 writes ~4 GB).
e.g. --steps 20 --param m=127 --param n=63 --param p=63 (~2 GB, ~1 min to compare).
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mfclib  # noqa: E402

TOOLCHAIN = mfclib.MFC_DIR / "toolchain"


def _import_packer():
    """MFC's packer needs MFC's own Python environment (rich, mako, ...)."""
    sys.path.insert(0, str(TOOLCHAIN))
    try:
        from mfc.packer import pack, tol  # noqa: PLC0415
    except ImportError:
        venv = mfclib.MFC_DIR / "build" / "venv"
        venv_py = venv / "bin" / "python"
        # (venv pythons are symlinks to the system one, so compare prefixes)
        if venv_py.exists() and Path(sys.prefix).resolve() != venv.resolve():
            os.execv(str(venv_py), [str(venv_py), "-I", __file__] + sys.argv[1:])
        raise SystemExit("cannot import MFC's packer; run ./mfc.sh once to create build/venv")
    return pack, tol


def compare(args: argparse.Namespace) -> int:
    pack, tol = _import_packer()
    ref_dir, run_dir = Path(args.reference).resolve(), Path(args.run).resolve()
    packs = []
    for d in (ref_dir, run_dir):
        if not (d / "D").is_dir():
            print(f"{d}: no D/ directory (run it with runmfc.sh --keep)", file=sys.stderr)
            return 2
        p, err = pack.compile(str(d))
        if err is not None:
            print(f"{d}: {err}", file=sys.stderr)
            return 2
        packs.append(p)
    golden, candidate = packs

    print(f"reference: {ref_dir}\ncandidate: {run_dir}\n")
    worst_abs = worst_rel = 0.0
    nan_files = []
    rows = []
    for g in sorted(golden.entries.values(), key=lambda e: e.filepath):
        c = candidate.find(g.filepath)
        if c is None:
            rows.append((g.filepath, "missing", math.inf, math.inf))
            continue
        if len(c.doubles) != len(g.doubles):
            rows.append((g.filepath, f"{len(c.doubles)} vs {len(g.doubles)}", math.inf, math.inf))
            continue
        fa = fr = 0.0
        scale = max((abs(v) for v in g.doubles if not math.isnan(v)), default=0.0)
        for gv, cv in zip(g.doubles, c.doubles):
            if math.isnan(cv) or math.isnan(gv):
                if not (math.isnan(cv) and math.isnan(gv)):
                    nan_files.append(g.filepath)
                    break
                continue
            d = abs(cv - gv)
            fa = max(fa, d)
            # Relative to the field's magnitude: pointwise ratios blow up near zero.
            if scale > 0:
                fr = max(fr, d / scale)
        worst_abs, worst_rel = max(worst_abs, fa), max(worst_rel, fr)
        rows.append((g.filepath, f"{len(g.doubles)}", fa, fr))

    width = max(len(r[0]) for r in rows) if rows else 10
    print(f"{'file':<{width}}  {'values':>10}  {'max |diff|':>11}  {'max |diff|/max|ref|':>19}")
    for name, n, a, r in rows if args.verbose else sorted(rows, key=lambda r: r[3], reverse=True)[:args.top]:
        print(f"{name:<{width}}  {n:>10}  {a:>11.3e}  {r:>19.3e}")
    if not args.verbose and len(rows) > args.top:
        print(f"... {len(rows) - args.top} more files (--verbose lists all)")
    print(f"\nworst over all files: abs {worst_abs:.3e}, rel-to-field-max {worst_rel:.3e}")
    if nan_files:
        print(f"NaN where the reference has a number: {len(set(nan_files))} file(s), e.g. {nan_files[0]}")

    tolerance = tol.Tolerance(args.abstol, args.reltol)
    err, msg = tol.compare(candidate, golden, tolerance)
    if msg is None:
        print(f"\nOK: within MFC tolerance ({tolerance}); average error {err}")
        return 0
    print(f"\nFAIL: not within MFC tolerance ({tolerance}):\n{msg}")
    return 1


def summary(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve() if args.dir else mfclib.RUNS_DIR
    results = [r for r in mfclib.load_results(root) if not args.case or r.get("case") == args.case]
    if not results:
        print(f"no result.json under {root}")
        return 1

    # Speedup is against the plain run (double, no case-opt, no fastmath) of
    # the same case, arguments and rank count.
    def problem(r):
        return (r.get("case"), tuple(r.get("case_args") or []), r.get("ranks"))

    base: dict[tuple, float] = {}
    for r in results:
        if (r.get("grind_ns") and r.get("status") == "ok" and r.get("precision") == "double"
                and not r.get("case_opt") and not r.get("fastmath")):
            base.setdefault(problem(r), r["grind_ns"])

    ok = sorted((r for r in results if r.get("status") == "ok"), key=lambda r: r["grind_ns"])
    bad = [r for r in results if r.get("status") != "ok"]
    header = f"{'grind ns/gp/eq/rhs':>18}  {'speedup':>7}  {'sim s':>8}  {'case':<26} {'args':<20} config / run"
    print(header)
    print("-" * len(header))
    for r in ok:
        b = base.get(problem(r))
        sp = f"{b / r['grind_ns']:.2f}x" if b else "-"
        sim = f"{r['sim_exec_s']:.1f}" if r.get("sim_exec_s") is not None else "-"
        print(f"{r['grind_ns']:>18.5g}  {sp:>7}  {sim:>8}  {r.get('case', '?'):<26} "
              f"{' '.join(r.get('case_args') or []):<20} {mfclib.config_tag(r)}  [{Path(r['run_dir']).name}]")
    for r in bad:
        print(f"{r.get('status', '?'):>18}  {'':>7}  {'':>8}  {r.get('case', '?'):<26} "
              f"{' '.join(r.get('case_args') or []):<20} {mfclib.config_tag(r)}  [{Path(r['run_dir']).name}]")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare", help="compare a run's D/ output against a reference run")
    c.add_argument("reference")
    c.add_argument("run")
    c.add_argument("--abstol", type=float, default=1e-10)
    c.add_argument("--reltol", type=float, default=1e-6)
    c.add_argument("--top", type=int, default=10, help="files to list (worst first)")
    c.add_argument("--verbose", action="store_true", help="list every file")
    s = sub.add_parser("summary", help="table of runs, fastest first")
    s.add_argument("dir", nargs="?", default=None, help="default: outputs/mfc_runs")
    s.add_argument("--case", default=None, help="only this case slug")
    args = p.parse_args()
    return compare(args) if args.cmd == "compare" else summary(args)


if __name__ == "__main__":
    sys.exit(main())
