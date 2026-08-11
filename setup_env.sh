#!bin/bash

#export CC=gcc
#export CXX=g++
#export FC=gfortran

export CFLAGS="-O3 -march=native -ffast-math -funroll-loops"
export CXXFLAGS="$CFLAGS"
export FCFLAGS="$CFLAGS"

export LDFLAGS="-march=native"

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "Error: this script must be sourced:"
    echo "  source $0"
    exit 1
fi

SCC_DIR=$(pwd)
if [[ "$(basename "$SCC_DIR")" == "SCC-CONNECT-2026" ]]; then
    echo "Setting up environment"
else
    echo "please setup inside SCC-CONNECT-2026"
    exit 1
fi

git submodule sync --recursive
git submodule update --init --recursive --remote

source HPC-Tools/add_build.sh
source HPC-Tools/basic_build.sh
export INSTALL_DIR=$SCC_DIR/libs/opt
export BUILD_DIR=$SCC_DIR/libs/build
export CLONE_DIR=$SCC_DIR/libs/clone

if command -v python3 &>/dev/null; then
    echo "Python exists"
    if [[ -d "./venv" ]]; then
    	echo "venv exists"
    else
    	echo "venv not found"
	python3 -m venv --prompt SCC-CONNECT venv
    fi
    if [[ -n "$VIRTUAL_ENV" ]]; then
    	deactivate
    fi
    source venv/bin/activate
    echo "Using SCC-CONNECT venv"
else
    echo "Python not found"
fi
