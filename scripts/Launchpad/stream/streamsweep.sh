wget https://www.cs.virginia.edu/stream/FTP/Code/stream.c

spack load intel-oneapi-compilers
python3 scripts/Launchpad/stream/streamsweep.py \
  --source ./stream.c \
  --compiler icx \
  --max-array-gib 500 \
  --max-threads 128 \
  --output outputs/stream

python3 scripts/Launchpad/stream/streambest.py \
    outputs/stream/trials.jsonl