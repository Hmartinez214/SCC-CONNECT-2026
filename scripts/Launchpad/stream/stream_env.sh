# shellcheck shell=bash
# Environment for building and running STREAM on Launchpad; sourced (from
# the repo root) by stream_smac3.sh and runstream.sh. Returns non-zero if
# something is missing.

# shellcheck disable=SC1091
source ./launchpad_env.sh
cd "$SCC_DIR" || return 1

# Same icx as the earlier random sweep (its results are the priors), plus
# Intel MPI for MPI_RANKS > 1. launchpad_env.sh puts HPC-X's Open MPI on PATH,
# so Intel MPI's bin goes first explicitly.
spack load intel-oneapi-compilers intel-oneapi-mpi || return 1
IMPI="$(spack location -i intel-oneapi-mpi)" || return 1
export PATH="$IMPI/mpi/latest/bin:$PATH"
# ...and HPC-X's include dirs (CPATH) would shadow Intel MPI's mpi.h.
CPATH="$(tr ':' '\n' <<<"${CPATH:-}" | grep -v /hpcx/ | paste -sd:)"
export CPATH
if ! command -v mpiicx >/dev/null || ! mpirun --version 2>&1 | grep -q "Intel(R) MPI"; then
    echo "Intel MPI (mpiicx / mpirun) not found" >&2
    return 1
fi

# Official MPI STREAM (libs/clone is git-ignored, so fetch it once).
MPI_SRC=libs/clone/stream_mpi/stream_mpi.c
MPI_SHA=2d1e0afd2f0906f510587fa4e297aca8b93cdc7516bd78e2b83f413694c2a9be
if [[ ! -f "$MPI_SRC" ]]; then
    mkdir -p "$(dirname "$MPI_SRC")"
    curl -fsSL -o "$MPI_SRC" https://www.cs.virginia.edu/stream/FTP/Code/Versions/stream_mpi.c || return 1
fi
if [[ "$(sha256sum "$MPI_SRC" | cut -d" " -f1)" != "$MPI_SHA" ]]; then
    echo "$MPI_SRC: unexpected sha256 (expected $MPI_SHA)" >&2
    return 1
fi
