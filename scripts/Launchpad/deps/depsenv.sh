#!/usr/bin/env bash
# Put the libraries built by builddeps.sh on PATH / LD_LIBRARY_PATH / CPATH /
# CMAKE_PREFIX_PATH via HPC-Tools' add_build. Must be sourced:
#
#   source scripts/Launchpad/deps/depsenv.sh            # OpenBLAS + Open MPI
#   source scripts/Launchpad/deps/depsenv.sh openblas   # just one

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "Error: this script must be sourced: source $0"
    exit 1
fi

_DEPS_SCC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck disable=SC1091
source "$_DEPS_SCC_DIR/HPC-Tools/add_build.sh"
_DEPS_OPT="$_DEPS_SCC_DIR/libs/opt"

_deps=("$@")
[[ ${#_deps[@]} -eq 0 ]] && _deps=(openblas openmpi)
for _dep in "${_deps[@]}"; do
    case "$_dep" in
        openblas)
            add_build "$_DEPS_OPT/OpenBLAS" openblas
            # OpenBLAS's CMake install puts its headers in include/openblas/.
            export CPATH="$_DEPS_OPT/OpenBLAS/include/openblas:${CPATH:-}"
            export C_INCLUDE_PATH="$_DEPS_OPT/OpenBLAS/include/openblas:${C_INCLUDE_PATH:-}"
            export CPLUS_INCLUDE_PATH="$_DEPS_OPT/OpenBLAS/include/openblas:${CPLUS_INCLUDE_PATH:-}"
            export OPENBLAS_INCLUDE_DIR="$_DEPS_OPT/OpenBLAS/include/openblas"
            ;;
        openmpi)  add_build "$_DEPS_OPT/openmpi-5.0.11" openmpi ;;
        *) echo "depsenv: unknown library '$_dep'" ;;
    esac
done
unset _dep _deps _DEPS_OPT _DEPS_SCC_DIR
