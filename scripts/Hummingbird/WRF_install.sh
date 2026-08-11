#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# WRF installation for SCC-CONNECT-2026 / Hummingbird
#
# Source:
#   libs/clone/WRF
#
# Build:
#   libs/build/WRF
#
# Install:
#   libs/opt/WRF
#
# This script does NOT modify the WRF submodule.
# ------------------------------------------------------------

# Resolve repository root from this script's location.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

WRF_SRC="${REPO_ROOT}/libs/clone/WRF"
WRF_BUILD="${REPO_ROOT}/libs/build/WRF"
WRF_OPT="${REPO_ROOT}/libs/opt/WRF"

JOBS="${JOBS:-12}"

echo "============================================================"
echo "WRF installation"
echo "============================================================"
echo "Repository : ${REPO_ROOT}"
echo "Source     : ${WRF_SRC}"
echo "Build      : ${WRF_BUILD}"
echo "Install    : ${WRF_OPT}"
echo "Jobs       : ${JOBS}"
echo "============================================================"

# ------------------------------------------------------------
# Basic checks
# ------------------------------------------------------------

if [[ ! -d "${REPO_ROOT}/.git" ]]; then
    echo "ERROR: Could not identify SCC-CONNECT-2026 repository root."
    exit 1
fi

if [[ ! -f "${WRF_SRC}/configure_new" ]]; then
    echo "ERROR: WRF source not found:"
    echo "       ${WRF_SRC}"
    echo
    echo "Initialize the submodule first:"
    echo "    git submodule update --init --recursive"
    exit 1
fi

if ! command -v module >/dev/null 2>&1; then
    echo "ERROR: Environment Modules/Lmod is not available."
    exit 1
fi

# ------------------------------------------------------------
# Modules
# ------------------------------------------------------------

echo
echo "Loading modules..."

module purge

module load gnu13/13.2.0
module load openmpi5/5.0.5
module load netcdf/4.9.2
module load netcdf-fortran/4.6.1
module load cmake

echo
echo "Loaded modules:"
module list

# ------------------------------------------------------------
# Verify required tools
# ------------------------------------------------------------

echo
echo "Checking compiler and dependency tools..."

required_commands=(
    gcc
    gfortran
    mpicc
    mpif90
    nc-config
    nf-config
    cmake
)

for cmd in "${required_commands[@]}"; do
    if ! command -v "${cmd}" >/dev/null 2>&1; then
        echo "ERROR: Required command not found: ${cmd}"
        exit 1
    fi

    printf "  %-12s %s\n" "${cmd}" "$(command -v "${cmd}")"
done

echo
echo "NetCDF:"
nc-config --version
nf-config --version

# ------------------------------------------------------------
# Create build/install directories
# ------------------------------------------------------------

mkdir -p "${WRF_BUILD}"
mkdir -p "${WRF_OPT}"

# ------------------------------------------------------------
# WRF configure_new
#
# configure_new currently assumes that:
#
#     build_dir/..
#
# is the WRF source directory.
#
# Since our source and build directories are separated, use a
# temporary copy of configure_new with the source path corrected.
#
# The WRF submodule itself is never modified.
# ------------------------------------------------------------

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

PATCHED_CONFIGURE="${TMP_DIR}/configure_new"

python3 - "${WRF_SRC}/configure_new" "${PATCHED_CONFIGURE}" "${WRF_SRC}" <<'PY'
import pathlib
import sys

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
wrf_src = sys.argv[3]

text = src.read_text()

# configure_new contains:
#
#     cd "$buildDirectory"
#     cmake .. ...
#
# Replace only the CMake source-directory argument.
old = 'cmake .. -DCMAKE_INSTALL_PREFIX='
new = f'cmake "{wrf_src}" -DCMAKE_INSTALL_PREFIX='

if old not in text:
    print("ERROR: Could not find expected configure_new CMake command.")
    print("The WRF version may have changed its configure_new implementation.")
    sys.exit(1)

text = text.replace(old, new, 1)

dst.write_text(text)
dst.chmod(src.stat().st_mode)
PY

echo
echo "Using temporary configure_new wrapper:"
echo "  ${PATCHED_CONFIGURE}"
echo
echo "WRF source remains unmodified."

# ------------------------------------------------------------
# Configure
# ------------------------------------------------------------

cd "${WRF_SRC}"

echo
echo "============================================================"
echo "Configuring WRF"
echo "============================================================"
echo
echo "Select the desired WRF options when prompted."
echo
echo "Recommended initial configuration:"
echo "  WRF_CORE    : ARW"
echo "  WRF_CASE    : EM_REAL"
echo "  DM / MPI    : Y"
echo "  SM / OpenMP : N"
echo

"${PATCHED_CONFIGURE}" \
    -d "${WRF_BUILD}" \
    -i "${WRF_OPT}"

# ------------------------------------------------------------
# Compile
# ------------------------------------------------------------

echo
echo "============================================================"
echo "Compiling WRF"
echo "============================================================"
echo

cd "${WRF_SRC}"

./compile_new -d "${WRF_BUILD}" -j "${JOBS}"

# ------------------------------------------------------------
# Verify installation
# ------------------------------------------------------------

echo
echo "============================================================"
echo "WRF installation complete"
echo "============================================================"

if [[ -x "${WRF_OPT}/bin/wrf" ]]; then
    echo "  wrf  : ${WRF_OPT}/bin/wrf"
else
    echo "WARNING: wrf executable was not found at:"
    echo "         ${WRF_OPT}/bin/wrf"
fi

if [[ -x "${WRF_OPT}/bin/real" ]]; then
    echo "  real : ${WRF_OPT}/bin/real"
fi

if [[ -x "${WRF_OPT}/bin/ideal" ]]; then
    echo "  ideal: ${WRF_OPT}/bin/ideal"
fi

echo
echo "Build directory:"
echo "  ${WRF_BUILD}"

echo
echo "Install directory:"
echo "  ${WRF_OPT}"

echo
echo "============================================================"
