#!/usr/bin/env python3
"""Which MFC optimizations help where: per-case effects grouped by problem
size and physics.

    venv/bin/python scripts/Launchpad/mfc/mfc_conclusions.py            # markdown tables to stdout
    venv/bin/python scripts/Launchpad/mfc/mfc_conclusions.py --csv out.csv

Uses the same data and paired comparison as plot_mfc_params.py (each run vs
the same settings with one variable at its default; per case the median),
and reads each case's grid and physics flags from a simulation.inp it left
behind (outputs/mfc/runs or outputs/mfc/tuning_runs).
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mfclib  # noqa: E402
import plot_mfc_params as pmp  # noqa: E402

#: simulation.inp flags that mark a physics feature (value T unless noted).
FEATURES = {
    "viscous": "viscous", "igr": "igr", "ib": "ib (immersed boundary)",
    "bubbles_euler": "bubbles (Euler)", "bubbles_lagrange": "bubbles (Lagrange)", "qbmm": "QBMM",
    "mhd": "MHD", "chemistry": "chemistry", "hypoelasticity": "hypoelasticity",
    "hyperelasticity": "hyperelasticity", "surface_tension": "surface tension",
    "acoustic_source": "acoustic source", "cyl_coord": "axisymmetric/cylindrical",
    "relax": "phase change", "relativity": "relativistic",
}
#: (variable, value) effects reported, in order.
EFFECTS = [("CASE_OPT", "on"), ("RDMA_MPI", "on"), ("BIND", "numa"), ("BIND", "cores"), ("FASTMATH", "on")]


def read_inp(case: str) -> dict[str, str]:
    """Parameters of one simulation.inp written for this case."""
    f = mfclib.REPO / "outputs" / "mfc" / "runs" / "cases" / case / "simulation.inp"
    if not f.exists():
        # Any other run of the case. result.json's run_dir may predate a move
        # of outputs/, so use the directory the file is in now.
        f = next((res.parent / "simulation.inp" for res in sorted((mfclib.REPO / "outputs" / "mfc").rglob("result.json"))
                  if (res.parent / "simulation.inp").exists() and f'"case": "{case}"' in res.read_text()), None)
    if f is None:
        return {}
    out = {}
    for line in f.read_text(errors="replace").splitlines():
        m = re.match(r"\s*([A-Za-z_%()0-9]+)\s*=\s*(.+?)\s*$", line)
        if m:
            out.setdefault(m.group(1), m.group(2))
    return out


def describe(case: str) -> dict:
    p = read_inp(case)
    cells = 1
    dims = 0
    for k in ("m", "n", "p"):
        v = int(float(p.get(k, 0)))
        cells *= v + 1
        dims += v > 0
    feats = [name for key, name in FEATURES.items() if p.get(key, "F").upper().startswith("T")]
    if p.get("recon_type") == "2":
        feats.append("MUSCL")
    return {"case": case, "dims": dims, "cells": cells, "features": feats,
            "num_fluids": int(p.get("num_fluids", 1) or 1), "weno_order": p.get("weno_order", "-")}


def size_class(cells: int) -> str:
    if cells < 1e5:
        return "a) < 100k cells"
    if cells < 1e6:
        return "b) 100k - 1M"
    if cells < 1e7:
        return "c) 1M - 10M"
    return "d) >= 10M"


def table(rows: list[dict], key, title: str, min_n: int = 2) -> str:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        for k in key(r):
            groups.setdefault(k, []).append(r)
    head = ["", "cases"] + [f"{v} {val}" for v, val in EFFECTS]
    lines = [f"### {title}", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for g in sorted(groups):
        rs = groups[g]
        if len(rs) < min_n:
            continue
        cells = []
        for v, val in EFFECTS:
            xs = [r[(v, val)] for r in rs if r.get((v, val)) is not None]
            cells.append(f"{statistics.median(xs):.2f}x" if xs else "-")
        lines.append(f"| {g} | {len(rs)} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", default=None, help="also write one row per case here")
    args = ap.parse_args()
    data = pmp.load(min_runs=20)
    rows = []
    for case in data:
        r = describe(case)
        r["group"] = pmp.group(case)
        for v, val in EFFECTS:
            d = pmp.paired({case: data[case]}, v).get(case, [])
            xs = [x["speedup"] for x in d if x["cfg"][v] == val]
            r[(v, val)] = statistics.median(xs) if xs else None
        plain = [x["grind"] for x in data[case] if all(x["cfg"][k] == dv for k, dv in {k: pmp.VARIABLES[k][0][0] for k in pmp.VARIABLES}.items())]
        r["plain_grind_ns"] = statistics.mean(plain) if plain else None
        rows.append(r)

    print(f"{len(rows)} cases (unstable ones left out; see plot_mfc_params.py)\n")
    print(table(rows, lambda r: [r["group"]], "By case group"))
    print(table(rows, lambda r: [size_class(r["cells"])], "By grid size (total cells, 2 GPUs)"))
    print(table(rows, lambda r: r["features"] or ["(none of these)"], "By physics feature (a case counts in each of its features)", min_n=3))
    print(table(rows, lambda r: [f"weno_order {r['weno_order']}"], "By WENO order", min_n=3))

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["case", "group", "dims", "cells", "features", "plain_grind_ns"] + [f"{v}={val}" for v, val in EFFECTS])
            for r in rows:
                w.writerow([r["case"], r["group"], r["dims"], r["cells"], ";".join(r["features"]), r["plain_grind_ns"]]
                           + [r[(v, val)] for v, val in EFFECTS])


if __name__ == "__main__":
    main()
