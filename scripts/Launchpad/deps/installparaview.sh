#!/usr/bin/env bash
# Install the ParaView binary release locally into libs/opt/paraview-<version>,
# with libs/opt/paraview pointing at it. No root needed.
#
#   scripts/Launchpad/deps/installparaview.sh                 # 6.0.1
#   PARAVIEW_VER=6.0.0 scripts/Launchpad/deps/installparaview.sh
#
# Use it headless (no X server): libs/opt/paraview/bin/pvpython script.py.
# The release ships EGL (GPU) and OSMesa (CPU) offscreen backends; render3d.sh
# picks one through VTK_DEFAULT_OPENGL_WINDOW.

set -euo pipefail
SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
VER="${PARAVIEW_VER:-6.0.1}"
NAME="ParaView-$VER-MPI-Linux-Python3.12-x86_64"
URL="https://www.paraview.org/files/v${VER%.*}/$NAME.tar.gz"
DEST="$SCC_DIR/libs/opt/paraview-$VER"

mkdir -p "$SCC_DIR/libs/clone" "$SCC_DIR/libs/opt"
cd "$SCC_DIR/libs/clone"
[[ -f "$NAME.tar.gz" ]] || wget -q --show-progress "$URL"
rm -rf "$DEST"; mkdir -p "$DEST"
tar -xzf "$NAME.tar.gz" -C "$DEST" --strip-components=1
ln -sfn "paraview-$VER" "$SCC_DIR/libs/opt/paraview"

echo "installed $NAME -> libs/opt/paraview-$VER (libs/opt/paraview)"
"$DEST/bin/pvpython" --version
