#!/usr/bin/env bash
# Build MFC with GPU (OpenACC) support using the NVIDIA HPC SDK.
#By default, passes --gpu acc and -j $(nproc)
# Checks for dependencies, installs anything missing with apt (asks for sudo),
# then builds MFC. Does not run apt upgrade or touch NVIDIA drivers.
#
# Usage:
#   scripts/buildmfc.sh [--clean] [-j N] [-- extra mfc.sh build args]
#
#If receiving permission errors: chmod +x scripts/Launchpad/mfc/buildmfc.sh
#
# Examples:
#   scripts/Launchpad/mfc/buildmfc.sh
#   scripts/Launchpad/mfc/buildmfc.sh --clean
#   scripts/Launchpad/mfc/buildmfc.sh -j 64 -- --fastmath
#   MFC_NVHPC_VERSION=25.11 scripts/Launchpad/mfc/buildmfc.sh --clean
#
# Environment overrides:
#   MFC_NVHPC_VERSION  NVIDIA HPC SDK version (default 25.9; 26.9 lacks
#                      libnvhpcwrapnvtx, which MFC links against)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
MFC_SUBMODULE="libs/clone/MFC"
MFC_DIR="$REPO_ROOT/$MFC_SUBMODULE"
export MFC_NVHPC_VERSION="${MFC_NVHPC_VERSION:-25.9}"

JOBS="$(nproc)"
CLEAN=0
EXTRA_ARGS=()

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
    case "$1" in
        --clean)   CLEAN=1 ;;
        -j)        shift; JOBS="${1:?-j needs a number}" ;;
        -j*)       JOBS="${1#-j}" ;;
        -h|--help) usage ;;
        --)        shift; EXTRA_ARGS=("$@"); break ;;
        *)         die "unknown option: $1 (see --help)" ;;
    esac
    shift
done

# True if version $1 is strictly greater than version $2.
version_gt() {
    [ "$1" != "$2" ] && [ "$(printf '%s\n' "$1" "$2" | sort -V | tail -n1)" = "$1" ]
}

# ---------------------------------------------------------------- platform
command -v apt-get >/dev/null || die "this script expects Ubuntu/Debian (apt-get)"

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)  DEB_ARCH=amd64 ;;
    aarch64) DEB_ARCH=arm64 ;;
    *)       die "unsupported architecture: $ARCH" ;;
esac

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
elif command -v sudo >/dev/null; then
    SUDO="sudo"
else
    SUDO="__nosudo__"
fi

APT_UPDATED=0
apt_install() {
    [ "$SUDO" = "__nosudo__" ] && die "need root or sudo to install: $*"
    if [ "$APT_UPDATED" -eq 0 ]; then
        info "Updating apt package lists"
        $SUDO apt-get update -y
        APT_UPDATED=1
    fi
    info "Installing: $*"
    $SUDO apt-get install -y "$@"
}

# ---------------------------------------------------------------- system packages
SYSTEM_PKGS=(tar wget curl git make cmake gcc g++ python3 python3-dev python3-venv
             ca-certificates gnupg)
missing=()
for pkg in "${SYSTEM_PKGS[@]}"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if [ "${#missing[@]}" -gt 0 ]; then
    apt_install "${missing[@]}"
else
    info "System packages present"
fi

# ---------------------------------------------------------------- GPU driver
DRIVER_CUDA=""
if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
    DRIVER_CUDA="$(nvidia-smi | grep -oP 'CUDA Version:\s*\K[0-9.]+' || true)"
    info "NVIDIA driver supports CUDA ${DRIVER_CUDA:-unknown}"
else
    warn "nvidia-smi not working; MFC will build but cannot run on GPUs here"
fi

# ---------------------------------------------------------------- NVIDIA HPC SDK
NVHPC_DIR="/opt/nvidia/hpc_sdk/Linux_${ARCH}/${MFC_NVHPC_VERSION}"
NVHPC_PKG="nvhpc-${MFC_NVHPC_VERSION//./-}"

if [ -x "$NVHPC_DIR/compilers/bin/nvfortran" ]; then
    info "NVIDIA HPC SDK ${MFC_NVHPC_VERSION} present"
else
    if [ ! -f /etc/apt/sources.list.d/nvhpc.list ]; then
        [ "$SUDO" = "__nosudo__" ] && die "need root or sudo to add the NVIDIA HPC SDK repo"
        info "Adding NVIDIA HPC SDK apt repository"
        curl -fsSL https://developer.download.nvidia.com/hpc-sdk/ubuntu/DEB-GPG-KEY-NVIDIA-HPC-SDK \
            | $SUDO gpg --dearmor --yes -o /usr/share/keyrings/nvidia-hpcsdk-archive-keyring.gpg
        echo "deb [signed-by=/usr/share/keyrings/nvidia-hpcsdk-archive-keyring.gpg] https://developer.download.nvidia.com/hpc-sdk/ubuntu/${DEB_ARCH} /" \
            | $SUDO tee /etc/apt/sources.list.d/nvhpc.list >/dev/null
        APT_UPDATED=0
    fi
    apt_install "$NVHPC_PKG"
    [ -x "$NVHPC_DIR/compilers/bin/nvfortran" ] || die "nvfortran not found in $NVHPC_DIR after install"
fi

# Bundled CUDA must not be newer than what the driver supports.
SDK_CUDA="$(ls "$NVHPC_DIR/cuda" 2>/dev/null | grep -E '^[0-9]+\.[0-9]+$' | sort -V | tail -n1 || true)"
if [ -n "$DRIVER_CUDA" ] && [ -n "$SDK_CUDA" ] && version_gt "$SDK_CUDA" "$DRIVER_CUDA"; then
    warn "SDK bundles CUDA $SDK_CUDA but the driver only supports $DRIVER_CUDA;"
    warn "install ${NVHPC_PKG}-cuda-multi and export NVHPC_DEFAULT_CUDA=<older version>"
fi

if ! find "$NVHPC_DIR" -name 'libnvhpcwrapnvtx*' -print -quit 2>/dev/null | grep -q .; then
    warn "libnvhpcwrapnvtx not found in SDK ${MFC_NVHPC_VERSION}; MFC will likely fail to link"
fi

# ---------------------------------------------------------------- build environment
# shellcheck source=mfcenv.sh
source "$SCRIPT_DIR/mfcenv.sh"

# GCC-specific flags from other setup scripts break the NVIDIA compilers.
unset CFLAGS CXXFLAGS FFLAGS FCFLAGS LDFLAGS

for tool in nvc nvc++ nvfortran mpicc mpif90 mpirun; do
    command -v "$tool" >/dev/null || die "$tool not found on PATH"
done
case "$(command -v mpif90)" in
    "$NVHPC"/*) ;;
    *) warn "mpif90 resolves to $(command -v mpif90), not the SDK's MPI" ;;
esac
info "Using $(nvfortran --version | grep -m1 -oE 'nvfortran [0-9.]+')"

# ---------------------------------------------------------------- MFC source
if [ ! -x "$MFC_DIR/mfc.sh" ]; then
    info "Initializing MFC submodule"
    git -C "$REPO_ROOT" submodule update --init "$MFC_SUBMODULE"
fi

# ---------------------------------------------------------------- build
cd "$MFC_DIR"
if [ "$CLEAN" -eq 1 ]; then
    info "Cleaning previous MFC build"
    ./mfc.sh clean
fi

info "Building MFC (--gpu acc, -j $JOBS)"
./mfc.sh build --gpu acc -j "$JOBS" "${EXTRA_ARGS[@]}"

echo
info "MFC built. To run cases in a new shell:"
echo "      source ${SCRIPT_DIR#"$REPO_ROOT"/}/mfcenv.sh"
echo "      cd $MFC_SUBMODULE && ./mfc.sh run <case.py> -n 2"