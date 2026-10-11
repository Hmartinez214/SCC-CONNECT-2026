#!/usr/bin/env bash
# Build WRF (ARW core) with GCC + system OpenMPI + NetCDF from apt, using
# WRF's classic ./configure and ./compile.
#
# Checks for dependencies and installs anything missing with apt (asks for
# sudo). Does not run apt upgrade.
#
# Usage:
#   scripts/Launchpad/wrf/buildwrf.sh [--case CASE] [--clean] [-j N]
#
#   --case CASE  WRF case to compile (default em_real). em_real builds in
#                libs/clone/WRF; any other case (em_quarter_ss, em_b_wave,
#                em_tropical_cyclone, ...) builds in its own copy at
#                libs/build/WRF_<case>, so the em_real build stays intact.
#   --clean      wipe the previous build and reconfigure
#   -j N         parallel compile jobs (default 16; WRF gains little past that)
#
# Environment overrides:
#   WRF_CONFIG_OPT  ./configure menu choice (default 34 = GNU dmpar/MPI;
#                   35 = GNU dm+sm/MPI+OpenMP)
#   WRF_NEST_OPT    nesting choice (default 1 = basic)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
WRF_SUBMODULE="libs/clone/WRF"
WRF_SRC="$REPO_ROOT/$WRF_SUBMODULE"
WRF_CONFIG_OPT="${WRF_CONFIG_OPT:-34}"
WRF_NEST_OPT="${WRF_NEST_OPT:-1}"

CASE="em_real"
CLEAN=0
JOBS=16

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
    case "$1" in
        --case)    shift; CASE="${1:?--case needs a name}" ;;
        --case=*)  CASE="${1#--case=}" ;;
        --clean)   CLEAN=1 ;;
        -j)        shift; JOBS="${1:?-j needs a number}" ;;
        -j*)       JOBS="${1#-j}" ;;
        -h|--help) usage ;;
        *)         die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

# ---------------------------------------------------------------- system packages
command -v apt-get >/dev/null || die "this script expects Ubuntu/Debian (apt-get)"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
elif command -v sudo >/dev/null; then
    SUDO="sudo"
else
    SUDO="__nosudo__"
fi

PKGS=(csh m4 perl make rsync git gcc g++ gfortran
      libnetcdf-dev libnetcdff-dev libhdf5-dev libopenmpi-dev openmpi-bin)
missing=()
for pkg in "${PKGS[@]}"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if [ "${#missing[@]}" -gt 0 ]; then
    [ "$SUDO" = "__nosudo__" ] && die "need root or sudo to install: ${missing[*]}"
    info "Installing: ${missing[*]}"
    $SUDO apt-get update -y
    $SUDO apt-get install -y "${missing[@]}"
else
    info "System packages present"
fi

# ---------------------------------------------------------------- build environment
# shellcheck source=wrfenv.sh
source "$SCRIPT_DIR/wrfenv.sh"

for tool in gfortran gcc cpp mpif90 mpicc mpirun csh m4 perl; do
    command -v "$tool" >/dev/null || die "$tool not found on PATH"
done
[ "$(command -v mpif90)" = /usr/bin/mpif90 ] \
    || warn "mpif90 is $(command -v mpif90), expected the system OpenMPI at /usr/bin/mpif90"
case "$(mpif90 --showme:command 2>/dev/null || true)" in
    *gfortran*) ;;
    *) die "mpif90 does not wrap gfortran (got: $(mpif90 --showme:command 2>&1))" ;;
esac
info "Using $(gfortran --version | head -n1), $(mpirun --version 2>&1 | head -n1)"

# NetCDF: WRF wants include/ and lib/ under one prefix.
MULTIARCH="$(gcc -print-multiarch)"
mkdir -p "$NETCDF"
ln -sfn /usr/include "$NETCDF/include"
ln -sfn "/usr/lib/$MULTIARCH" "$NETCDF/lib"
[ -e "$NETCDF/lib/libnetcdff.so" ] || die "libnetcdff.so not found in /usr/lib/$MULTIARCH"
[ -e "$NETCDF/include/netcdf.inc" ] || die "netcdf.inc not found in /usr/include"
info "NETCDF=$NETCDF"

# ---------------------------------------------------------------- WRF source
if [ ! -x "$WRF_SRC/configure" ]; then
    info "Initializing WRF submodule"
    git -C "$REPO_ROOT" submodule update --init "$WRF_SUBMODULE"
fi
# WRF 4.5+ pulls some physics (e.g. MYNN) in as its own submodules.
if [ -f "$WRF_SRC/.gitmodules" ]; then
    git -C "$WRF_SRC" submodule update --init --recursive
fi
[ -d "$WRF_SRC/test/$CASE" ] || die "unknown case '$CASE'; available: $(ls "$WRF_SRC/test" | tr '\n' ' ')"

if [ "$CASE" = em_real ]; then
    BUILD_DIR="$WRF_SRC"
else
    BUILD_DIR="$REPO_ROOT/libs/build/WRF_$CASE"
    info "Syncing WRF source to ${BUILD_DIR#"$REPO_ROOT"/}"
    mkdir -p "$BUILD_DIR"
    rsync -a --exclude='.git' "$WRF_SRC/" "$BUILD_DIR/"
fi
cd "$BUILD_DIR"

# ---------------------------------------------------------------- configure
if [ "$CLEAN" -eq 1 ]; then
    info "Cleaning previous build"
    ./clean -a >/dev/null
fi

if [ ! -f configure.wrf ]; then
    info "Configuring (menu option $WRF_CONFIG_OPT, nesting $WRF_NEST_OPT)"
    printf '%s\n%s\n' "$WRF_CONFIG_OPT" "$WRF_NEST_OPT" | ./configure > "configure_${CASE}.log" 2>&1 || true
    [ -f configure.wrf ] || die "configure failed; see ${BUILD_DIR}/configure_${CASE}.log"
fi

if ! grep -qE '^SFC[[:space:]]*=[[:space:]]*gfortran' configure.wrf \
   || ! grep -qE '^DMPARALLEL[[:space:]]*=[[:space:]]*1' configure.wrf; then
    die "configure.wrf is not a GNU + MPI setup. Run ./configure by hand in $BUILD_DIR
       to see the menu numbers, then rerun with WRF_CONFIG_OPT=<number> and --clean."
fi

# ---------------------------------------------------------------- compile
LOG="compile_${CASE}.log"
info "Compiling $CASE with -j $JOBS (20-40 min; log: ${BUILD_DIR}/$LOG)"
./compile -j "$JOBS" "$CASE" > "$LOG" 2>&1 || true

if [ "$CASE" = em_real ]; then
    EXES=(wrf.exe real.exe)
else
    EXES=(wrf.exe ideal.exe)
fi
for exe in "${EXES[@]}"; do
    if [ ! -x "main/$exe" ]; then
        grep -nE -m 20 'Error|error:' "$LOG" >&2 || tail -n 30 "$LOG" >&2
        die "main/$exe was not built; see ${BUILD_DIR}/$LOG"
    fi
done

echo
info "WRF $CASE built: ${EXES[*]} in ${BUILD_DIR#"$REPO_ROOT"/}/main"
echo "      To run in a new shell:"
echo "      source ${SCRIPT_DIR#"$REPO_ROOT"/}/wrfenv.sh"
echo "      cd ${BUILD_DIR#"$REPO_ROOT"/}/test/$CASE"