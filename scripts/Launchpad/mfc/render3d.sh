#!/usr/bin/env bash
# Headless 3D rendering of MFC output with ParaView: mfc2vtk.py (MFC's venv)
# converts post_process output to RUN_DIR/vtk/, then render3d.py (pvpython) renders it.
#
#   scripts/Launchpad/mfc/render3d.sh RUN_DIR --list-vars
#   scripts/Launchpad/mfc/render3d.sh RUN_DIR --var qm --iso 5000 --color-by omega_mag --step all --mp4
#   scripts/Launchpad/mfc/render3d.sh RUN_DIR --var alpha1 --mode volume
#   scripts/Launchpad/mfc/render3d.sh RUN_DIR --help        # all options
#
# RUN_DIR needs post_process output (silo_hdf5/ or binary/), e.g. from
#   scripts/Launchpad/mfc/runmfc.sh --post --steps 8000 --param t_step_save=400 3D_TaylorGreenVortex
#
# Environment:
#   PARAVIEW         ParaView install (default libs/opt/paraview, from deps/installparaview.sh)
#   RENDER_BACKEND   egl (GPU, default) | osmesa (CPU)
#   EGL_DEVICE       GPU index for egl (default 0)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PARAVIEW="${PARAVIEW:-$REPO_ROOT/libs/opt/paraview}"

[[ -x "$PARAVIEW/bin/pvpython" ]] \
    || { echo "no pvpython in $PARAVIEW; run scripts/Launchpad/deps/installparaview.sh" >&2; exit 2; }

case "${RENDER_BACKEND:-egl}" in
    egl)    export VTK_DEFAULT_OPENGL_WINDOW=vtkEGLRenderWindow
            export VTK_DEFAULT_EGL_DEVICE_INDEX="${EGL_DEVICE:-0}" ;;
    osmesa) export VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow ;;
    *)      echo "RENDER_BACKEND must be egl or osmesa" >&2; exit 2 ;;
esac

# ffmpeg for --mp4: the system one, else the one MFC's venv ships (imageio-ffmpeg).
if [[ -z "${MFC_FFMPEG:-}" ]]; then
    MFC_FFMPEG="$(command -v ffmpeg || ls "$REPO_ROOT"/libs/clone/MFC/build/venv/lib/python3*/site-packages/imageio_ffmpeg/binaries/ffmpeg-* 2>/dev/null | head -1 || true)"
fi
export MFC_FFMPEG

MFC_PY="$REPO_ROOT/libs/clone/MFC/build/venv/bin/python"
[[ -x "$MFC_PY" ]] || { echo "no MFC venv at $MFC_PY; build MFC first" >&2; exit 2; }
[[ $# -gt 0 && $1 != -* ]] || exec "$PARAVIEW/bin/pvpython" "$SCRIPT_DIR/render3d.py" --help
"$MFC_PY" "$SCRIPT_DIR/mfc2vtk.py" "$@"
for a in "$@"; do [[ $a == --list-vars ]] && exit 0; done

exec "$PARAVIEW/bin/pvpython" --force-offscreen-rendering "$SCRIPT_DIR/render3d.py" "$@"
