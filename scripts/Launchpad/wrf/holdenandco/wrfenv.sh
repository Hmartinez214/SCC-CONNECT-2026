# Environment for building and running WRF with GCC + system OpenMPI + NetCDF.
#
#   source scripts/Launchpad/wrf/wrfenv.sh
#
# Sourced automatically by buildwrf.sh. Source it yourself before running
# WRF in a new shell. It removes NVIDIA HPC SDK, HPC-X and Spack entries from
# PATH and LD_LIBRARY_PATH (HPC-X ships a libmpi with the same name as the
# system OpenMPI one), so don't combine it with mfcenv.sh in the same shell.

# Print colon-separated list $1 without NVHPC / HPC-X / Spack entries.
_wrf_filter() {
    local IFS=: out="" p
    for p in $1; do
        case "$p" in
            ""|*hpc_sdk*|*hpcx*|*spack*) ;;
            *) out="${out:+$out:}$p" ;;
        esac
    done
    printf '%s' "$out"
}

PATH="$(_wrf_filter "$PATH")"
export PATH
if [ -n "${LD_LIBRARY_PATH:-}" ]; then
    LD_LIBRARY_PATH="$(_wrf_filter "$LD_LIBRARY_PATH")"
    export LD_LIBRARY_PATH
fi
unset -f _wrf_filter

# Compiler settings from other setups (e.g. CC=nvc, GCC -ffast-math) break WRF's build.
unset CC CXX FC F77 F90 CFLAGS CXXFLAGS FFLAGS FCFLAGS LDFLAGS

# WRF wants NetCDF's include/ and lib/ under one directory; buildwrf.sh
# creates this folder of links into Ubuntu's split system layout.
_wrf_root="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null)"
export NETCDF="${_wrf_root}/libs/build/netcdf-gnu"
unset _wrf_root

export WRFIO_NCD_LARGE_FILE_SUPPORT=1
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
ulimit -s unlimited 2>/dev/null || true