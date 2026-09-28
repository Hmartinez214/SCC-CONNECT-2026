#!/usr/bin/env bash
# Build WPS (serial, gfortran) against the GNU em_real WRF build from
# buildwrf.sh. WPS downloads and builds its own GRIB2 libraries
# (zlib, libpng, JasPer) via --build-grib2-libs.
#
# Usage:
#   scripts/Launchpad/wrf/holdenandco/buildwps.sh [--clean]
#
#   --clean   wipe the previous WPS build and reconfigure
#
# Environment overrides:
#   WPS_CONFIG_OPT  ./configure menu choice (default 1 = gfortran serial)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
WPS_SUBMODULE="libs/clone/WPS"
WPS_DIR="$REPO_ROOT/$WPS_SUBMODULE"
export WRF_DIR="$REPO_ROOT/libs/clone/WRF"
WPS_CONFIG_OPT="${WPS_CONFIG_OPT:-1}"
CLEAN=0

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --clean)   CLEAN=1 ;;
        -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)         die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

# ---------------------------------------------------------------- dependencies
missing=()
for pkg in zlib1g-dev libpng-dev csh m4 perl make gfortran gcc; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if [ "${#missing[@]}" -gt 0 ]; then
    info "Installing: ${missing[*]}"
    sudo apt-get update -y
    sudo apt-get install -y "${missing[@]}"
fi

# shellcheck source=wrfenv.sh
source "$SCRIPT_DIR/wrfenv.sh"

[ -x "$WRF_DIR/main/wrf.exe" ] && [ -x "$WRF_DIR/main/real.exe" ] \
    || die "no em_real WRF build in $WRF_DIR; run buildwrf.sh first"
[ -d "$NETCDF/lib" ] || die "NETCDF=$NETCDF missing; run buildwrf.sh first"

if [ ! -x "$WPS_DIR/configure" ]; then
    info "Initializing WPS submodule"
    git -C "$REPO_ROOT" submodule update --init "$WPS_SUBMODULE"
fi
cd "$WPS_DIR"

# ---------------------------------------------------------------- configure
if [ "$CLEAN" -eq 1 ]; then
    info "Cleaning previous WPS build"
    ./clean -a >/dev/null 2>&1 || true
fi

if [ ! -f configure.wps ]; then
    info "Configuring WPS (menu option $WPS_CONFIG_OPT, building GRIB2 libs)"
    printf '%s\n' "$WPS_CONFIG_OPT" | ./configure --build-grib2-libs > configure.log 2>&1 || true
    [ -f configure.wps ] || die "configure failed; see $WPS_DIR/configure.log"
fi
grep -qE '^SFC[[:space:]]*=[[:space:]]*gfortran' configure.wps \
    || die "configure.wps is not a gfortran setup; rerun with --clean (and WPS_CONFIG_OPT=<n>)"

# ---------------------------------------------------------------- compile
info "Compiling WPS (log: $WPS_DIR/compile.log)"
./compile > compile.log 2>&1 || true

for exe in geogrid.exe ungrib.exe metgrid.exe; do
    if [ ! -e "$exe" ]; then
        grep -nE -m 20 'Error|error:' compile.log >&2 || tail -n 30 compile.log >&2
        die "$exe was not built; see $WPS_DIR/compile.log"
    fi
done

echo
info "WPS built: geogrid.exe ungrib.exe metgrid.exe in $WPS_SUBMODULE"