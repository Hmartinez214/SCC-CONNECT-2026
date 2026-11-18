#!/usr/bin/env bash
# Run every line of experiments.txt (or a subset matching $1 regex), train + evaluate,
# spreading jobs over the visible GPUs with $PER_GPU concurrent jobs each.
#   ./run_experiments.sh            # all
#   ./run_experiments.sh 'abl'      # only ablations
#   EXTRA="train.steps=20000" ./run_experiments.sh D_rdmoe
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
PER_GPU=${PER_GPU:-3}
NGPU=$(nvidia-smi -L | wc -l)
RUNS=${RUNS:-runs}
mkdir -p "$RUNS"
grep -v '^#' experiments.txt | grep -E "${1:-.}" | awk 'NF' > "$RUNS/.queue"
i=0
while IFS='|' read -r name cfg ov; do
  name=$(echo $name); cfg=$(echo $cfg)
  gpu=$(( i % NGPU )); i=$((i+1))
  echo "$gpu|$name|$cfg|$ov"
done < "$RUNS/.queue" | xargs -P $((PER_GPU*NGPU)) -I{} bash -c '
  IFS="|" read -r gpu name cfg ov <<< "{}"
  out='"$RUNS"'/$name
  if [ -f $out/eval.json ]; then echo "skip $name"; exit 0; fi
  echo "start $name (gpu $gpu)"
  CUDA_VISIBLE_DEVICES=$gpu '"$PY"' -m rdmoe.train --config configs/$cfg.yaml --out $out --set $ov '"${EXTRA:-}"' > $out.log 2>&1 \
   && CUDA_VISIBLE_DEVICES=$gpu '"$PY"' -m rdmoe.evaluate $out >> $out.log 2>&1 \
   && echo "done $name" || echo "FAILED $name (see $out.log)"'
