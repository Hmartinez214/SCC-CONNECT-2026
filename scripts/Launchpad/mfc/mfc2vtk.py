"""Convert MFC post_process output (Silo-HDF5 or binary) to VTK rectilinear
grids (.vtr, one per step) plus a ParaView time series (series.pvd).

    <MFC venv python> mfc2vtk.py RUN_DIR [--step all] [--out DIR] [--list-vars]

Uses MFC's own readers (toolchain/mfc/viz), so it runs under MFC's venv
(render3d.sh calls it). ParaView 6.0's VisIt Silo reader segfaults on load,
so render3d.py reads these files instead. Values are cell centered and written
as float32 point data on the cell-center coordinates; omega1-3 also give
omega_mag. Existing .vtr files are reused.

pick() and read_pvd() are also used by render3d.py (pvpython), so this module
imports MFC's reader only inside main().
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MFC_TOOLCHAIN = REPO / "libs" / "clone" / "MFC" / "toolchain"


def pick(steps: list[int], spec: str) -> list[int]:
    """Steps selected by 'last', 'first', 'all', N, 'N,M,...' or 'start:end:stride'."""
    if spec == "last":
        return steps[-1:]
    if spec == "first":
        return steps[:1]
    if spec == "all":
        return list(steps)
    if ":" in spec:
        a, b, *c = (int(x) if x else None for x in spec.split(":"))
        wanted = set(range(a or 0, (b if b is not None else steps[-1]) + 1, (c[0] if c and c[0] else 1)))
    else:
        wanted = {int(x) for x in spec.split(",")}
    out = [s for s in steps if s in wanted]
    if not out:
        raise SystemExit(f"--step {spec}: none of the saved steps {steps[0]}..{steps[-1]} match")
    return out


def read_pvd(pvd: Path) -> dict[int, Path]:
    """Step -> .vtr file listed in a series.pvd written here."""
    return {int(m.group(1)): pvd.parent / m.group(2)
            for m in re.finditer(r'timestep="(\d+)"[^>]*file="([^"]+)"', pvd.read_text())}


def write_vtr(path: Path, x, y, z, arrays: dict) -> None:
    """VTK XML RectilinearGrid, raw appended binary (little endian, UInt64 headers)."""
    import numpy as np

    blocks, offset, xml = [], 0, {"point": [], "coord": []}
    items = [("point", k, a) for k, a in arrays.items()] + [("coord", k, a) for k, a in (("x", x), ("y", y), ("z", z))]
    for kind, name, a in items:
        data = np.asarray(a, dtype="<f4").ravel(order="F").tobytes()
        xml[kind].append(f'<DataArray type="Float32" Name="{name}" format="appended" offset="{offset}"/>')
        blocks.append(np.uint64(len(data)).tobytes() + data)
        offset += 8 + len(data)
    point_xml, coord_xml = xml["point"], xml["coord"]
    ext = f"0 {len(x) - 1} 0 {len(y) - 1} 0 {len(z) - 1}"
    head = (
        '<?xml version="1.0"?>\n'
        '<VTKFile type="RectilinearGrid" version="1.0" byte_order="LittleEndian" header_type="UInt64">\n'
        f'<RectilinearGrid WholeExtent="{ext}"><Piece Extent="{ext}">\n'
        f"<PointData>{''.join(point_xml)}</PointData>\n"
        f"<Coordinates>{''.join(coord_xml)}</Coordinates>\n"
        "</Piece></RectilinearGrid>\n"
        '<AppendedData encoding="raw">_'
    )
    tmp = path.with_suffix(".vtr.tmp")
    with open(tmp, "wb") as f:
        f.write(head.encode())
        for b in blocks:
            f.write(b)
        f.write(b"</AppendedData></VTKFile>\n")
    tmp.replace(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input", type=Path, help="case/run directory with silo_hdf5/ or binary/")
    ap.add_argument("--step", default="last")
    ap.add_argument("--out", type=Path, help="default RUN_DIR/vtk")
    ap.add_argument("--list-vars", action="store_true")
    args, _ = ap.parse_known_args()  # render3d.sh passes the renderer's options too

    import numpy as np

    sys.path.insert(0, str(MFC_TOOLCHAIN))
    from mfc.viz import reader, silo_reader

    case = str(args.input.resolve())
    fmt = reader.discover_format(case)
    steps = reader.discover_timesteps(case, fmt)
    if not steps:
        raise SystemExit(f"no post_process output under {case}")
    load = silo_reader.assemble_silo if fmt == "silo" else (lambda d, s: reader.assemble(d, s, fmt))

    if args.list_vars:
        d = load(case, steps[-1])
        names = sorted(d.variables) + (["omega_mag"] if {"omega1", "omega2", "omega3"} <= set(d.variables) else [])
        print(f"{case} ({fmt}): {len(steps)} saved steps {steps[0]}..{steps[-1]}, "
              f"grid {len(d.x_cc)}x{len(d.y_cc)}x{len(d.z_cc)}")
        print("variables:", " ".join(names))
        return

    out = (args.out or args.input / "vtk").resolve()
    out.mkdir(parents=True, exist_ok=True)
    for s in pick(steps, args.step):
        f = out / f"step_{s:08d}.vtr"
        if f.exists():
            continue
        d = load(case, s)
        if d.ndim != 3:
            raise SystemExit(f"{case} is {d.ndim}D; use ./mfc.sh viz for 1D/2D data")
        v = dict(d.variables)
        if {"omega1", "omega2", "omega3"} <= set(v):
            v["omega_mag"] = np.sqrt(v["omega1"] ** 2 + v["omega2"] ** 2 + v["omega3"] ** 2)
        write_vtr(f, d.x_cc, d.y_cc, d.z_cc, v)
        print(f"wrote {f}", flush=True)

    # Series of every converted step, so earlier conversions stay available.
    files = sorted(out.glob("step_*.vtr"))
    entries = "".join(f'<DataSet timestep="{int(p.stem[5:])}" file="{p.name}"/>\n' for p in files)
    (out / "series.pvd").write_text(
        '<?xml version="1.0"?>\n<VTKFile type="Collection" version="1.0">\n<Collection>\n'
        f"{entries}</Collection>\n</VTKFile>\n")


if __name__ == "__main__":
    main()
