"""Render MFC 3D output with ParaView: isosurfaces, volume or slice, to PNG
frames and optionally an MP4. Runs under pvpython on the VTK series that
mfc2vtk.py writes; use render3d.sh, which converts first and sets up headless
(EGL/OSMesa) rendering and ffmpeg.

    render3d.sh RUN_DIR --list-vars
    render3d.sh RUN_DIR --var qm --iso 1e5 --color-by omega_mag --step all --mp4
    render3d.sh RUN_DIR --var alpha1 --mode volume --step last
    render3d.sh RUN_DIR --var pres --mode slice --normal 0 1 0

RUN_DIR is a case directory with post_process output (e.g. runmfc.sh --post),
converted to RUN_DIR/vtk/series.pvd. Frames go to RUN_DIR/render/<var>_<mode>/
unless --out is given.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from paraview import simple as pv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mfc2vtk import pick, read_pvd  # noqa: E402


def data_range(src, var: str, times: list[float]) -> tuple[float, float]:
    lo, hi = float("inf"), float("-inf")
    for t in times:
        src.UpdatePipeline(t)
        r = src.PointData[var].GetRange()
        lo, hi = min(lo, r[0]), max(hi, r[1])
    return lo, hi


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input", type=Path)
    ap.add_argument("--var", help="variable to contour / volume render / slice (see --list-vars)")
    ap.add_argument("--mode", choices=("iso", "volume", "slice"), default="iso")
    ap.add_argument("--iso", type=float, nargs="+", help="isosurface values (default: --iso-frac)")
    ap.add_argument("--iso-frac", type=float, nargs="+", default=[0.5],
                    help="isosurfaces at these fractions of --var's range (default 0.5)")
    ap.add_argument("--color-by", help="color isosurfaces by this variable (default: solid color)")
    ap.add_argument("--normal", type=float, nargs=3, default=(0, 0, 1), help="slice normal (default z)")
    ap.add_argument("--origin", type=float, nargs=3, help="slice origin (default: domain center)")
    ap.add_argument("--step", default="last", help="last | first | all | N | N,M,... | start:end:stride")
    ap.add_argument("--cmap", default="Viridis (matplotlib)", help="ParaView colormap preset")
    ap.add_argument("--vmin", type=float)
    ap.add_argument("--vmax", type=float)
    ap.add_argument("--opacity", type=float, default=1.0, help="isosurface opacity")
    ap.add_argument("--resample", type=int, default=256, help="--mode volume: points per side of the resampled image")
    ap.add_argument("--size", default="1920x1080", help="image size WxH")
    ap.add_argument("--azimuth", type=float, default=35.0)
    ap.add_argument("--elevation", type=float, default=25.0)
    ap.add_argument("--zoom", type=float, default=1.0)
    ap.add_argument("--background", choices=("dark", "white"), default="dark")
    ap.add_argument("--out", type=Path, help="output dir (default RUN_DIR/render/<var>_<mode>)")
    ap.add_argument("--mp4", action="store_true", help="also encode the frames to <out>.mp4")
    ap.add_argument("--fps", type=int, default=10)
    args = ap.parse_args()

    run_dir = args.input.resolve()
    pvd = run_dir / "vtk" / "series.pvd"
    if not pvd.is_file():
        raise SystemExit(f"no {pvd}; run render3d.sh (it converts with mfc2vtk.py first)")
    steps = sorted(read_pvd(pvd))
    reader = pv.PVDReader(FileName=str(pvd))
    reader.UpdatePipeline(float(steps[-1]))
    variables = sorted(reader.PointData.keys())
    if not args.var:
        raise SystemExit(f"--var is required; have: {' '.join(variables)}")
    for v in (args.var, args.color_by):
        if v and v not in variables:
            raise SystemExit(f"unknown variable {v}; have: {' '.join(variables)}")

    which = pick(steps, args.step)
    sel_times = [float(s) for s in which]
    out = args.out or run_dir / "render" / f"{args.var}_{args.mode}"
    out.mkdir(parents=True, exist_ok=True)
    w, h = (int(x) for x in args.size.lower().split("x"))

    view = pv.CreateView("RenderView")
    view.ViewSize = [w, h]
    view.OrientationAxesVisibility = 1
    try:
        pv.LoadPalette("WhiteBackground" if args.background == "white" else "BlueGrayBackground")
    except Exception:
        pass
    fg = [0, 0, 0] if args.background == "white" else [1, 1, 1]

    points = reader  # cell-center values, written as point data by mfc2vtk.py
    outline = pv.Show(pv.Outline(Input=reader), view)
    outline.AmbientColor = outline.DiffuseColor = fg

    color_var = args.color_by if args.mode == "iso" else args.var
    if args.mode == "iso":
        if args.iso:
            levels = args.iso
        else:
            lo, hi = data_range(points, args.var, [sel_times[-1]])
            levels = [lo + f * (hi - lo) for f in args.iso_frac]
        src = pv.Contour(Input=points, ContourBy=["POINTS", args.var], Isosurfaces=levels, ComputeScalars=1)
        disp = pv.Show(src, view)
        disp.Opacity = args.opacity
        print(f"isosurfaces of {args.var} at {', '.join(f'{x:.6g}' for x in levels)}")
    elif args.mode == "volume":
        src = pv.ResampleToImage(Input=points, SamplingDimensions=[args.resample] * 3)
        disp = pv.Show(src, view)
        disp.SetRepresentationType("Volume")
    else:
        src = pv.Slice(Input=points)
        src.SliceType = "Plane"
        src.SliceType.Normal = list(args.normal)
        if args.origin:
            src.SliceType.Origin = list(args.origin)
        else:
            b = reader.GetDataInformation().GetBounds()
            src.SliceType.Origin = [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2]
        disp = pv.Show(src, view)

    if color_var:
        pv.ColorBy(disp, ("POINTS", color_var))
        lut = pv.GetColorTransferFunction(color_var)
        lut.ApplyPreset(args.cmap, True)
        lo, hi = args.vmin, args.vmax
        if lo is None or hi is None:
            dlo, dhi = data_range(points, color_var, sel_times)
            lo, hi = dlo if lo is None else lo, dhi if hi is None else hi
        lut.AutomaticRescaleRangeMode = "Never"
        lut.RescaleTransferFunction(lo, hi)
        if args.mode == "volume":
            pv.GetOpacityTransferFunction(color_var).RescaleTransferFunction(lo, hi)
        disp.SetScalarBarVisibility(view, True)
        bar = pv.GetScalarBar(lut, view)
        bar.Title = color_var
        bar.ComponentTitle = ""
        bar.TitleColor = bar.LabelColor = fg
    else:
        pv.ColorBy(disp, None)
        disp.AmbientColor = disp.DiffuseColor = [0.85, 0.55, 0.25]

    label = pv.Text()
    label_disp = pv.Show(label, view)
    label_disp.WindowLocation = "Upper Left Corner"
    label_disp.FontSize = 14
    label_disp.Color = fg

    view.ResetCamera()
    cam = view.GetActiveCamera()
    cam.Azimuth(args.azimuth)
    cam.Elevation(args.elevation)
    view.ResetCamera()
    cam.Dolly(args.zoom)

    frames = []
    for n, (step, t) in enumerate(zip(which, sel_times)):
        view.ViewTime = t
        label.Text = f"{args.var} ({args.mode})   step {step}"
        f = out / f"{args.var}_{args.mode}_{step:08d}.png"
        pv.SaveScreenshot(str(f), view, ImageResolution=[w, h])
        frames.append(f)
        print(f"[{n + 1}/{len(which)}] {f}", flush=True)

    if args.mp4:
        if len(frames) < 2:
            raise SystemExit("--mp4 needs more than one step (e.g. --step all)")
        ffmpeg = os.environ.get("MFC_FFMPEG", "ffmpeg")
        mp4 = out.with_suffix(".mp4")
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(args.fps),
                        "-pattern_type", "glob", "-i", str(out / f"{args.var}_{args.mode}_*.png"),
                        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        str(mp4)], check=True)
        print(mp4)


if __name__ == "__main__":
    main()
    # Skip interpreter teardown: ParaView 6.0's EGL window segfaults when destroyed.
    sys.stdout.flush()
    os._exit(0)
