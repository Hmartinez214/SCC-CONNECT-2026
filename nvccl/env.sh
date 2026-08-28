# Source before building or running:  source env.sh
#
# Discovers the toolchain and describes the GPU(s). Nothing machine-specific is
# hard-coded; override any of these by exporting them before sourcing:
#   CUDA_HOME     CUDA toolkit root        (default: module var, else /usr/local/cuda-*)
#   NCCL_HOME     dir with lib/ + include/ (default: ./nccl/build, else module var, else HPC SDK)
#   NVCC_GENCODE  gencode flags            (default: from each GPU's compute cap)
#   NGPUS         GPUs to benchmark        (default: number visible)

# --- CUDA ----------------------------------------------------------------
if [ -z "${CUDA_HOME:-}" ]; then
  if [ -n "${CUDATOOLKIT_HOME:-}" ] && [ -x "$CUDATOOLKIT_HOME/bin/nvcc" ]; then
    CUDA_HOME="$CUDATOOLKIT_HOME"
  else
    for d in $(ls -d /usr/local/cuda-* 2>/dev/null | sort -Vr) /usr/local/cuda; do
      [ -x "$d/bin/nvcc" ] && { CUDA_HOME="$d"; break; }
    done
  fi
fi
: "${CUDA_HOME:=/usr/local/cuda}"
export CUDA_HOME

# --- NCCL --------------------------------------------------------------
if [ -z "${NCCL_HOME:-}" ]; then
  if [ -d "$PWD/nccl/build/lib" ]; then
    NCCL_HOME="$PWD/nccl/build"                       # local source build
  elif [ -n "${NCCL_DIR:-}" ] && [ -e "$NCCL_DIR/include/nccl.h" ]; then
    NCCL_HOME="$NCCL_DIR"                             # `module load nccl` (Cray/NERSC)
  elif [ -n "${CRAY_NCCL_DIR:-}" ] && [ -e "$CRAY_NCCL_DIR/include/nccl.h" ]; then
    NCCL_HOME="$CRAY_NCCL_DIR"
  else
    NCCL_HOME=$(ls -d /opt/nvidia/hpc_sdk/*/*/comm_libs/*/nccl 2>/dev/null \
                 | xargs -r -n1 realpath 2>/dev/null | sort -Vu | tail -1)
  fi
fi
export NCCL_HOME="${NCCL_HOME:-}"

export LD_LIBRARY_PATH="${NCCL_HOME:+$NCCL_HOME/lib:}$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
export PATH="$CUDA_HOME/bin:$PATH"

# --- gencode: one -gencode per distinct compute capability present ------
if [ -z "${NVCC_GENCODE:-}" ]; then
  caps=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | tr -d ' .' | sort -u || true)
  gc=""
  for c in $caps; do gc="$gc -gencode=arch=compute_${c},code=sm_${c}"; done
  [ -n "$gc" ] || gc="-gencode=arch=compute_90,code=sm_90 -gencode=arch=compute_80,code=sm_80"
  export NVCC_GENCODE="${gc# }"
fi

# --- GPU count -------------------------------------------------------
: "${NGPUS:=$(nvidia-smi -L 2>/dev/null | grep -c '^GPU ' || true)}"
: "${NGPUS:=1}"
export NGPUS

# --- optional: WSL2 PTX-JIT workaround (no-op off WSL) ---------------
_wsljit=$(ls /usr/lib/wsl/drivers/*/libnvidia-ptxjitcompiler.so.1 2>/dev/null | head -1)
if [ -n "$_wsljit" ] && ls /usr/lib/x86_64-linux-gnu/libnvidia-ptxjitcompiler.so.5* >/dev/null 2>&1; then
  export LD_PRELOAD="${LD_PRELOAD:+$LD_PRELOAD:}$_wsljit"
fi
unset _wsljit

# --- notifications (optional; no-op unless NTFY_TOPIC + NTFY_TOKEN set) --
. "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/notify.sh" 2>/dev/null || true

# --- report --------------------------------------------------------
_nccl_ver=$(awk '/define NCCL_MAJOR/{a=$3}/define NCCL_MINOR/{b=$3}/define NCCL_PATCH/{c=$3}END{if(a)print a"."b"."c}' "${NCCL_HOME:-/nonexistent}/include/nccl.h" 2>/dev/null) || true
echo "CUDA_HOME    = $CUDA_HOME  ($($CUDA_HOME/bin/nvcc --version 2>/dev/null | sed -n 's/.*release \([0-9.]*\).*/\1/p'))"
echo "NCCL_HOME    = ${NCCL_HOME:-<none - load a module or run scripts/setup for source build>}  ${_nccl_ver:+(nccl $_nccl_ver)}"
echo "NVCC_GENCODE = $NVCC_GENCODE"
echo "NGPUS        = $NGPUS"
unset _nccl_ver
nvidia-smi --query-gpu=index,name,compute_cap,memory.total --format=csv,noheader 2>/dev/null | sed 's/^/  gpu /' || true
