#!/bin/bash
# Run the SMAC autotune as a batch job so it doesn't eat an interactive session.
#   sbatch tuning/tune.sbatch
# Change -A to your allocation's GPU project ("<project>_g").
#SBATCH -A m4007_g
#SBATCH -C gpu
#SBATCH -q shared
#SBATCH -t 4:00:00
#SBATCH -N 1
#SBATCH -n 1
#SBATCH -c 64
#SBATCH --gpus=2
#SBATCH -J nccl-tune
#SBATCH -o tuning/tune-%j.out

set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

module load cudatoolkit nccl python
source .venv/bin/activate                 # created on a login node: python -m venv .venv && pip install -r tuning/requirements.txt

# large-message regime (fast per trial). Add smaller ranges as separate studies.
python tuning/tune_smac.py --collective all_reduce     --trials 80 --beg 256M --end 8G
python tuning/tune_smac.py --collective reduce_scatter  --trials 80 --beg 256M --end 8G
python tuning/tune_smac.py --collective all_gather      --trials 60 --beg 256M --end 8G

echo "done. copy tuning/smac_out/ back to your laptop and: deepcave --open"
