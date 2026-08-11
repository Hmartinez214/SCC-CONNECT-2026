#!bin/bash


export CC=gcc
export CXX=g++
export FC=gfortran

export CFLAGS="-O3 -march=native -ffast-math -funroll-loops"
export CXXFLAGS="$CFLAGS"
export FCFLAGS="$CFLAGS"

export LDFLAGS="-march=nativ"
