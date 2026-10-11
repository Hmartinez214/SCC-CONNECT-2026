#!bin/bash

export OMP_NUM_THREADS=64
export KMP_AFFINITY=granularity=fine,compact,1,0
$(spack location -i stream %oneapi)/bin/stream_c.exe