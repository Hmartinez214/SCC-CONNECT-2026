#!/usr/bin/env bash
# Install Intel oneAPI (system-wide, /opt/intel/oneapi) from Intel's apt
# repository. Not built through HPC-Tools: Intel ships binaries only.
#
# Installs the HPC pieces rather than the full ~20 GB Base + HPC toolkits:
#   icx/icpx (DPC++/C++ compiler), ifx (Fortran compiler), Intel MPI, MKL.
#
# Usage:
#   scripts/Launchpad/deps/installoneapi.sh            # the components above
#   scripts/Launchpad/deps/installoneapi.sh --toolkits # full Base + HPC toolkits
#
# Afterwards, in a new shell:
#   source /opt/intel/oneapi/setvars.sh

set -euo pipefail

PACKAGES=(
    intel-oneapi-compiler-dpcpp-cpp
    intel-oneapi-compiler-fortran
    intel-oneapi-mpi-devel
    intel-oneapi-mkl-devel
)
if [[ "${1:-}" == "--toolkits" ]]; then
    PACKAGES=(intel-oneapi-base-toolkit intel-oneapi-hpc-toolkit)
fi

if [[ "$(id -u)" -eq 0 ]]; then SUDO=""; else SUDO="sudo"; fi

KEYRING=/usr/share/keyrings/oneapi-archive-keyring.gpg
LIST=/etc/apt/sources.list.d/oneAPI.list

if [[ ! -f "$LIST" ]]; then
    echo "==> Adding Intel oneAPI apt repository"
    wget -qO- https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB \
        | gpg --dearmor | $SUDO tee "$KEYRING" >/dev/null
    echo "deb [signed-by=$KEYRING] https://apt.repos.intel.com/oneapi all main" \
        | $SUDO tee "$LIST" >/dev/null
fi

echo "==> Installing: ${PACKAGES[*]}"
$SUDO apt-get update -y -o Dir::Etc::sourcelist="$LIST" -o Dir::Etc::sourceparts="-" -o APT::Get::List-Cleanup="0"
$SUDO apt-get install -y "${PACKAGES[@]}"

# setvars.sh is not nounset-safe.
set +u
# shellcheck disable=SC1091
source /opt/intel/oneapi/setvars.sh --force >/dev/null
set -u
echo "==> Installed:"
icx --version | head -1
ifx --version | head -1
mpirun --version | head -1
echo "MKLROOT=$MKLROOT"
echo
echo "Load it in a new shell with: source /opt/intel/oneapi/setvars.sh"
