#!bin/bash

spack install stream \
  +openmp \
  stream_array_size=268435456 \
  ntimes=100 \
  stream_type=double \
  offset=0 \
  cflags="-O3 -xCORE-AVX512 -qopt-zmm-usage=high -qopt-streaming-stores=always -mcmodel=medium" \
  %oneapi

# Intel website flags https://www.intel.com/content/www/us/en/developer/articles/technical/optimizing-memory-bandwidth-on-stream-triad.html
#    -DNTIMES=100 -DOFFSET=0 -DSTREAM_TYPE=double -DSTREAM_ARRAY_SIZE=268435456 \
#    -Wall -O3 -mcmodel=medium -qopenmp -shared-intel -qopt-streaming-stores always
# -xCORE-AVX512 -qopt-zmm-usage=high