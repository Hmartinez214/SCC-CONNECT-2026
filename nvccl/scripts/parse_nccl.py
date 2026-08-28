#!/usr/bin/env python3
"""Parse nccl-tests perf output (stdin or file) into JSON.

Emits per-size rows plus the summary "Avg bus bandwidth".
Columns in nccl-tests output:
    size count type redop root  time algbw busbw #wrong   time algbw busbw #wrong
    (B) (elts)                  (us) (GB/s)(GB/s)          (us) (GB/s)(GB/s)
The first triple is out-of-place, the second in-place.

Usage:
    ./all_reduce_perf ... | scripts/parse_nccl.py
    scripts/parse_nccl.py results/run.txt --csv
"""
import json
import re
import sys

NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|N/A"
ROW = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\w+)\s+(\S+)\s+(-?\d+)\s+"
    rf"({NUM})\s+({NUM})\s+({NUM})\s+(\S+)\s+"
    rf"({NUM})\s+({NUM})\s+({NUM})\s+(\S+)\s*$"
)
AVG = re.compile(r"Avg bus bandwidth\s*:\s*([\d.]+)")


def _f(x):
    try:
        return float(x)
    except ValueError:
        return None


def parse(text):
    rows, avg = [], None
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            m = AVG.search(line)
            if m:
                avg = float(m.group(1))
            continue
        m = ROW.match(line)
        if not m:
            continue
        g = m.groups()
        rows.append(
            dict(
                size=int(g[0]), count=int(g[1]), type=g[2], redop=g[3],
                oop_time_us=_f(g[5]), oop_algbw=_f(g[6]), oop_busbw=_f(g[7]),
                oop_wrong=g[8],
                ip_time_us=_f(g[9]), ip_algbw=_f(g[10]), ip_busbw=_f(g[11]),
                ip_wrong=g[12],
            )
        )
    peak = max(
        (r["ip_busbw"] for r in rows if r["ip_busbw"] is not None),
        default=None,
    )
    return dict(rows=rows, avg_busbw=avg, peak_busbw=peak)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    as_csv = "--csv" in sys.argv
    text = open(args[0]).read() if args else sys.stdin.read()
    out = parse(text)
    if as_csv:
        print("size,count,type,redop,oop_time_us,oop_busbw,ip_time_us,ip_busbw")
        for r in out["rows"]:
            print("{size},{count},{type},{redop},{oop_time_us},{oop_busbw},"
                  "{ip_time_us},{ip_busbw}".format(**r))
        print(f"# avg_busbw={out['avg_busbw']} peak_busbw={out['peak_busbw']}")
    else:
        json.dump(out, sys.stdout, indent=2)
        print()


if __name__ == "__main__":
    main()
